"""Pydantic v2 models for structured LLM output and schema conversion helpers.

All output models use strict configuration (`extra="forbid"` and required fields)
so they are compatible with OpenAI strict JSON schema structured outputs, while
the conversion helpers emit dicts conforming to `schema/trace.json` and
`schema/verdict.json`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RoleLiteral = Literal[
    "archmage",
    "engineer",
    "guardian_keeper",
    "historian",
    "oracle",
    "beast_master",
]


class _StrictModel(BaseModel):
    """Base model enforcing strict JSON schema compatibility."""

    model_config = ConfigDict(extra="forbid")


class RouteringDecision(_StrictModel):
    """Routing decision emitted by the King at each orchestration step."""

    aan: RoleLiteral | None = Field(
        ...,
        description="Target council role for the next turn, or null to stop the debate.",
    )
    vraag: str | None = Field(
        ...,
        description="Focused question for the chosen role, or null iff aan is null.",
    )
    reden: str = Field(
        ...,
        description="Concrete reasoning for choosing this role and question, or for stopping.",
    )
    open_vragen: list[str] = Field(
        ...,
        description="Remaining open questions after this routing decision; empty when stopping.",
    )
    werk_hypothese: str = Field(
        ...,
        description="Brief internal working summary of the current understanding across turns.",
    )


class CauseSlotOut(_StrictModel):
    """Single causal slot (claim, supporting evidence IDs, and confidence)."""

    bewering: str | None = Field(
        ...,
        description="Causal claim supported by evidence IDs, or null if honestly unknown.",
    )
    bewijs: list[str] = Field(
        ...,
        description="Unique evidence record IDs supporting this claim (empty iff bewering is null).",
    )
    zekerheid: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0.",
    )


class OorzaakOut(_StrictModel):
    """Three-slot causal structure required by the verdict schema."""

    ingreep: CauseSlotOut
    mechanisme: CauseSlotOut
    verloop: CauseSlotOut


class ActieOut(_StrictModel):
    """Ordered remediation action in the verdict recovery plan."""

    actie: str
    reden: str
    bewijs: list[str]


class RisicoOut(_StrictModel):
    """Identified risk and its mitigation strategy."""

    risico: str
    mitigatie: str


class VoorspellingOut(_StrictModel):
    """Optional forecast of when the incident will recur if unaddressed."""

    dag: int = Field(..., ge=1, description="Positive day number (>= 1).")
    bewering: str
    bewijs: list[str]


class StandpuntItem(_StrictModel):
    """Role-attributed stance entry used instead of an open dict in strict schema mode."""

    rol: RoleLiteral
    standpunt: str


class GeschilOut(_StrictModel):
    """Disagreement between council roles and its resolution, if any."""

    onderwerp: str
    standpunten: list[StandpuntItem]
    uitkomst: str | None
    bewijs: list[str]


class VerdictSynthesis(_StrictModel):
    """Full final synthesis produced by the King at the end of the debate."""

    koning_samenvatting_bericht: str = Field(
        ...,
        description="Closing speech by the King recorded in trace.json.",
    )
    samenvatting: str
    oorzaak: OorzaakOut
    acties: list[ActieOut]
    risicos: list[RisicoOut]
    voorspelling: VoorspellingOut | None
    geschillen: list[GeschilOut]


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def to_trace_geschillen(items: list[GeschilOut]) -> list[dict]:
    """Convert structured `GeschilOut` items into `schema/trace.json` dicts."""
    result: list[dict] = []
    for item in items:
        standpunten = {sp.rol: sp.standpunt for sp in item.standpunten}
        result.append(
            {
                "onderwerp": item.onderwerp,
                "standpunten": standpunten,
                "uitkomst": _clean_optional_text(item.uitkomst),
                "bewijs": _dedupe_preserve_order(item.bewijs),
            }
        )
    return result


def to_verdict_dict(run_id: str, synth: VerdictSynthesis) -> dict:
    """Convert `VerdictSynthesis` into a `schema/verdict.json`-compliant dict."""
    def _slot_dict(slot: CauseSlotOut) -> dict:
        return {
            "bewering": _clean_optional_text(slot.bewering),
            "bewijs": _dedupe_preserve_order(slot.bewijs),
            "zekerheid": slot.zekerheid,
        }

    verdict: dict = {
        "run_id": run_id,
        "samenvatting": synth.samenvatting,
        "oorzaak": {
            "ingreep": _slot_dict(synth.oorzaak.ingreep),
            "mechanisme": _slot_dict(synth.oorzaak.mechanisme),
            "verloop": _slot_dict(synth.oorzaak.verloop),
        },
        "acties": [
            {
                "actie": a.actie,
                "reden": a.reden,
                "bewijs": _dedupe_preserve_order(a.bewijs),
            }
            for a in synth.acties
        ],
        "risicos": [
            {
                "risico": r.risico,
                "mitigatie": r.mitigatie,
            }
            for r in synth.risicos
        ],
    }

    if synth.voorspelling is not None:
        verdict["voorspelling"] = {
            "dag": synth.voorspelling.dag,
            "bewering": synth.voorspelling.bewering,
            "bewijs": _dedupe_preserve_order(synth.voorspelling.bewijs),
        }

    return verdict
