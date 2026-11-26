"""Deterministic verification, normalization, and schema-repair helpers.

Zero LLM calls and zero domain-source facts. Validates cited record IDs
against the speaker's own retrieved source texts or previously spoken IDs in the
debate, normalizes Unicode dashes inside IDs, calibrates confidence scores, and
repairs verdict/dispute payloads to satisfy `schema/verdict.json` and
`schema/trace.json`.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

ROLE_TO_PREFIX: dict[str, str] = {
    "archmage": "ARC",
    "engineer": "ENG",
    "guardian_keeper": "GUA",
    "historian": "HIS",
    "oracle": "ORA",
    "beast_master": "BEA",
}
PREFIX_TO_ROLE: dict[str, str] = {v: k for k, v in ROLE_TO_PREFIX.items()}

VALID_ID_RE = re.compile(r"^(ARC|ENG|GUA|HIS|ORA|BEA)-[0-9]{3}$")
_ID_SEARCH_RE = re.compile(r"\b(?:ARC|ENG|GUA|HIS|ORA|BEA)-[0-9]{3}\b")
RARE_HYPHEN_ID_RE = re.compile(
    r"(ARC|ENG|GUA|HIS|ORA|BEA)[\u2010-\u2015\u2212]([0-9]{3})"
)


@dataclass
class MemberCitationReport:
    """Result of checking a council member's cited IDs against allowed sources."""

    valid_ids: list[str]
    invalid_own_ids: list[str]
    unspoken_foreign_ids: list[str]
    ok: bool


def normalize_hyphens(text: str) -> str:
    """Replace Unicode hyphens/dashes inside prefixed IDs with ASCII '-'."""
    return RARE_HYPHEN_ID_RE.sub(r"\1-\2", text)


def extract_ids(text: str) -> list[str]:
    """Normalize hyphens and return unique valid IDs in appearance order."""
    normalized = normalize_hyphens(text)
    seen: set[str] = set()
    ids: list[str] = []
    for match in _ID_SEARCH_RE.finditer(normalized):
        token = match.group(0)
        if token not in seen and VALID_ID_RE.match(token):
            seen.add(token)
            ids.append(token)
    return ids


def extract_ids_from_text_set(texts: list[str]) -> set[str]:
    """Extract all valid record IDs appearing across a list of text snippets."""
    found: set[str] = set()
    for text in texts:
        if text:
            # Handle JSON-escaped newlines (e.g. `\\nENG-001,...`) safely too
            unescaped = text.replace("\\r\\n", "\n").replace("\\n", "\n")
            found.update(extract_ids(unescaped))
    return found


def check_member_citations(
    role: str,
    message_text: str,
    own_source_texts: list[str],
    prior_debate_ids: set[str],
) -> MemberCitationReport:
    """Verify that IDs cited by a member are grounded in own sources or prior debate."""
    cited_ids = extract_ids(message_text)
    own_prefix = ROLE_TO_PREFIX.get(role)
    has_own_sources = len(own_source_texts) > 0
    own_available_ids = extract_ids_from_text_set(own_source_texts) if has_own_sources else set()
    normalized_prior = {normalize_hyphens(i.strip()) for i in prior_debate_ids}

    valid_ids: list[str] = []
    invalid_own_ids: list[str] = []
    unspoken_foreign_ids: list[str] = []

    for cid in cited_ids:
        prefix = cid.split("-", 1)[0]
        if prefix == own_prefix:
            if has_own_sources and cid not in own_available_ids:
                invalid_own_ids.append(cid)
            else:
                valid_ids.append(cid)
        else:
            if cid not in normalized_prior:
                unspoken_foreign_ids.append(cid)
            else:
                valid_ids.append(cid)

    ok = not invalid_own_ids and not unspoken_foreign_ids
    return MemberCitationReport(
        valid_ids=valid_ids,
        invalid_own_ids=invalid_own_ids,
        unspoken_foreign_ids=unspoken_foreign_ids,
        ok=ok,
    )


def calibrate_zekerheid(
    bewering: str | None,
    bewijs: list[str],
    raw_zekerheid: float | None,
    open_geschil_roles: set[str] | None = None,
) -> float:
    """Calibrate confidence based on cross-role evidence breadth."""
    if bewering is None or not str(bewering).strip() or not bewijs:
        return 0.0

    roles_in_evidence: set[str] = set()
    for raw_id in bewijs:
        norm_id = normalize_hyphens(str(raw_id).strip())
        if VALID_ID_RE.match(norm_id):
            prefix = norm_id.split("-", 1)[0]
            role = PREFIX_TO_ROLE.get(prefix)
            if role:
                roles_in_evidence.add(role)

    num_roles = len(roles_in_evidence)
    if num_roles == 0:
        return 0.0
    if num_roles == 1:
        cap = 0.75
    elif num_roles == 2:
        cap = 0.92
    else:
        cap = 0.98

    if open_geschil_roles and (open_geschil_roles & roles_in_evidence):
        cap = min(cap, 0.75)

    score = 0.7 if raw_zekerheid is None else float(raw_zekerheid)
    score = max(0.0, min(score, cap, 1.0))
    return round(score, 2)


def _normalize_nested(obj: Any) -> Any:
    if isinstance(obj, str):
        return normalize_hyphens(obj)
    if isinstance(obj, list):
        return [_normalize_nested(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _normalize_nested(v) for k, v in obj.items()}
    return obj


def _filter_bewijs(
    raw_bewijs: Any,
    allowed_ids: set[str],
    location: str,
    warnings: list[str],
) -> list[str]:
    if not isinstance(raw_bewijs, list):
        warnings.append(f"{location}: bewijs was not a list; reset to empty")
        return []

    filtered: list[str] = []
    seen: set[str] = set()
    for item in raw_bewijs:
        if not isinstance(item, str):
            warnings.append(f"{location}: dropped non-string bewijs entry {item!r}")
            continue
        norm = normalize_hyphens(item.strip())
        if not VALID_ID_RE.match(norm):
            warnings.append(f"{location}: dropped malformed ID {item!r}")
            continue
        if norm not in allowed_ids:
            warnings.append(f"{location}: dropped unspoken ID {norm!r}")
            continue
        if norm in seen:
            continue
        seen.add(norm)
        filtered.append(norm)
    return filtered


def verify_and_repair_verdict(
    verdict: dict,
    geschillen: list[dict],
    spoken_member_ids: set[str],
) -> tuple[dict, list[dict], list[str]]:
    """Normalize, filter unspoken IDs, and enforce schema conditionals on verdict/disputes."""
    clean_verdict: dict = _normalize_nested(copy.deepcopy(verdict))
    clean_geschillen_in: list[dict] = _normalize_nested(copy.deepcopy(geschillen))
    warnings: list[str] = []

    allowed_ids = {
        norm
        for raw_id in spoken_member_ids
        if isinstance(raw_id, str)
        for norm in (normalize_hyphens(raw_id.strip()),)
        if VALID_ID_RE.match(norm)
    }

    # Repair geschillen[] before oorzaak so open disputes inform confidence calibration
    valid_roles = set(ROLE_TO_PREFIX.keys())
    repaired_geschillen: list[dict] = []
    for idx, geschil in enumerate(clean_geschillen_in):
        if not isinstance(geschil, dict):
            warnings.append(f"geschillen[{idx}]: dropped non-object entry")
            continue

        raw_standpunten = geschil.get("standpunten")
        clean_standpunten: dict[str, str] = {}
        if isinstance(raw_standpunten, dict):
            for role_key, stance in raw_standpunten.items():
                if role_key not in valid_roles:
                    warnings.append(
                        f"geschillen[{idx}]: dropped invalid standpunten role {role_key!r}"
                    )
                    continue
                if not isinstance(stance, str):
                    warnings.append(
                        f"geschillen[{idx}]: dropped non-string stance for role {role_key!r}"
                    )
                    continue
                clean_standpunten[role_key] = stance

        if not clean_standpunten:
            warnings.append(
                f"geschillen[{idx}]: dropped because no valid standpunten remained"
            )
            continue

        geschil["standpunten"] = clean_standpunten
        geschil["bewijs"] = _filter_bewijs(
            geschil.get("bewijs", []),
            allowed_ids,
            f"geschillen[{idx}]",
            warnings,
        )

        uitkomst = geschil.get("uitkomst")
        if isinstance(uitkomst, str) and not uitkomst.strip():
            geschil["uitkomst"] = None
            uitkomst = None
        if isinstance(uitkomst, str) and not geschil["bewijs"]:
            geschil["uitkomst"] = None
            warnings.append(
                f"geschillen[{idx}]: cleared uitkomst because no valid spoken bewijs remained"
            )

        repaired_geschillen.append(geschil)

    open_geschil_roles: set[str] = {
        role_key
        for g in repaired_geschillen
        if g.get("uitkomst") is None
        for role_key in g["standpunten"]
    }

    # Repair oorzaak slots
    oorzaak = clean_verdict.get("oorzaak")
    if isinstance(oorzaak, dict):
        for slot_name in ("ingreep", "mechanisme", "verloop"):
            slot = oorzaak.get(slot_name)
            if not isinstance(slot, dict):
                continue
            filtered_ids = _filter_bewijs(
                slot.get("bewijs", []),
                allowed_ids,
                f"oorzaak.{slot_name}",
                warnings,
            )
            slot["bewijs"] = filtered_ids

            bewering = slot.get("bewering")
            if isinstance(bewering, str) and not bewering.strip():
                slot["bewering"] = None
                bewering = None
            if isinstance(bewering, str) and not filtered_ids:
                slot["bewering"] = None
                bewering = None
                warnings.append(
                    f"oorzaak.{slot_name}: cleared bewering because no valid spoken bewijs remained"
                )

            slot["zekerheid"] = calibrate_zekerheid(
                bewering,
                filtered_ids,
                slot.get("zekerheid"),
                open_geschil_roles=open_geschil_roles,
            )

    # Repair acties[]
    acties = clean_verdict.get("acties")
    if isinstance(acties, list):
        for idx, actie in enumerate(acties):
            if not isinstance(actie, dict):
                continue
            if "bewijs" in actie and actie["bewijs"] is not None:
                actie["bewijs"] = _filter_bewijs(
                    actie["bewijs"],
                    allowed_ids,
                    f"acties[{idx}]",
                    warnings,
                )
            elif "bewijs" in actie and actie["bewijs"] is None:
                del actie["bewijs"]

    # Repair optional voorspelling
    if "voorspelling" in clean_verdict:
        vp = clean_verdict["voorspelling"]
        if vp is None:
            del clean_verdict["voorspelling"]
        elif not isinstance(vp, dict):
            del clean_verdict["voorspelling"]
            warnings.append("voorspelling: dropped non-object value")
        else:
            vp_bewijs = _filter_bewijs(
                vp.get("bewijs", []),
                allowed_ids,
                "voorspelling",
                warnings,
            )
            vp["bewijs"] = vp_bewijs
            dag = vp.get("dag")
            bewering = vp.get("bewering")
            valid_dag = isinstance(dag, int) and not isinstance(dag, bool) and dag >= 1
            valid_bewering = isinstance(bewering, str) and len(bewering.strip()) >= 1
            if not vp_bewijs or not valid_dag or not valid_bewering:
                del clean_verdict["voorspelling"]
                warnings.append(
                    "voorspelling: dropped because bewijs is empty or dag/bewering is invalid"
                )

    return clean_verdict, repaired_geschillen, warnings
