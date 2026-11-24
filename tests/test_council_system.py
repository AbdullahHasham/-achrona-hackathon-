"""TDD test harness for the Council of Achrona orchestration and verification system.

Strictly uses generic synthetic fixtures (windmill / waterwheel / clockwork)
and synthetic 9xx record IDs so zero domain knowledge-base facts leak into repo code.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import council
import king
import raadsleden
import validate
import verify_gate
from schemas_pydantic import (
    ActieOut,
    CauseSlotOut,
    GeschilOut,
    OorzaakOut,
    RisicoOut,
    RouteringDecision,
    StandpuntItem,
    VerdictSynthesis,
    VoorspellingOut,
    to_trace_geschillen,
    to_verdict_dict,
)

TRACE_SCHEMA = json.loads((ROOT / "schema" / "trace.json").read_text(encoding="utf-8"))
VERDICT_SCHEMA = json.loads((ROOT / "schema" / "verdict.json").read_text(encoding="utf-8"))

EXPECTED_ROLES = {
    "archmage",
    "beast_master",
    "engineer",
    "guardian_keeper",
    "historian",
    "oracle",
}

UNICODE_DASHES = ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212")


def _schema_errors(instance: dict, schema: dict) -> list[str]:
    return [
        f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
        for e in Draft202012Validator(schema).iter_errors(instance)
    ]


def test_all_six_roles_present_and_valid() -> None:
    """All six expected council roles exist and expose non-empty metadata."""
    leden = raadsleden.alle()
    assert len(leden) == 6
    assert {lid.ROL for lid in leden} == EXPECTED_ROLES
    for lid in leden:
        assert isinstance(lid.ROL, str) and lid.ROL.strip()
        assert isinstance(lid.NAAM, str) and lid.NAAM.strip()
        assert isinstance(lid.PERSPECTIEF, str) and lid.PERSPECTIEF.strip()


def test_zero_leakage_audit() -> None:
    """No non-test Python module embeds literal record IDs or reads kennisbronnen in king.py."""
    literal_id_re = re.compile(r"\b(?:ARC|ENG|GUA|HIS|ORA|BEA)[-\u2010-\u2015\u2212][0-9]{3}\b")
    excluded_dirs = {"tests", ".venv", "voorbeelden", "__pycache__"}

    for py_path in sorted(ROOT.rglob("*.py")):
        rel = py_path.relative_to(ROOT)
        if any(part in excluded_dirs for part in rel.parts) or rel.name == "validate.py":
            continue
        source = py_path.read_text(encoding="utf-8")
        matches = literal_id_re.findall(source)
        assert matches == [], f"{rel} contains literal record IDs: {matches}"

    king_path = ROOT / "king.py"
    if king_path.exists():
        king_src = king_path.read_text(encoding="utf-8")
        assert "kennisbron_bestanden" not in king_src
        assert "kennisbronnen" not in king_src
        assert "MCPStreamableHTTPTool" not in king_src


def test_verify_gate_hyphen_normalization() -> None:
    """normalize_hyphens converts all 7 Unicode dash variants inside IDs to ASCII '-'."""
    for dash in UNICODE_DASHES:
        raw = f"Observation from ENG{dash}901 and ARC{dash}902."
        normalized = verify_gate.normalize_hyphens(raw)
        assert normalized == "Observation from ENG-901 and ARC-902."
        assert not validate.RARE_STREEP.search(normalized)

    mixed = "Compare HIS\u2011903, GUA\u2013904, BEA\u2014905, and ORA\u2212906."
    assert (
        verify_gate.normalize_hyphens(mixed)
        == "Compare HIS-903, GUA-904, BEA-905, and ORA-906."
    )


def test_verify_gate_member_citations() -> None:
    """check_member_citations catches hallucinated own IDs and unspoken foreign IDs."""
    own_sources = [
        "ENG-901 | day 2 | Waterwheel gear slip measured at 12 rpm.",
        "ENG-902 | day 3 | Millstone brake pad wear within nominal tolerance.",
    ]
    prior_debate_ids = {"ARC-901", "HIS-901"}

    # (a) Hallucinated own-role ID not present in own_sources
    bad_own = verify_gate.check_member_citations(
        role="engineer",
        message_text="Log ENG-999 proves the main axle snapped.",
        own_source_texts=own_sources,
        prior_debate_ids=prior_debate_ids,
    )
    assert bad_own.ok is False
    assert "ENG-999" in bad_own.invalid_own_ids
    assert "ENG-999" not in bad_own.valid_ids

    # (b) Unspoken foreign-role ID not present in prior_debate_ids
    bad_foreign = verify_gate.check_member_citations(
        role="engineer",
        message_text="Log ENG-901 aligns with GUA-999 from the gate ledger.",
        own_source_texts=own_sources,
        prior_debate_ids=prior_debate_ids,
    )
    assert bad_foreign.ok is False
    assert "GUA-999" in bad_foreign.unspoken_foreign_ids
    assert "ENG-901" in bad_foreign.valid_ids

    # (c) Valid own-role ID + previously spoken foreign ID (even with rare hyphen)
    good = verify_gate.check_member_citations(
        role="engineer",
        message_text="ENG\u2011901 confirms the gear slip noted alongside ARC-901.",
        own_source_texts=own_sources,
        prior_debate_ids=prior_debate_ids,
    )
    assert good.ok is True
    assert good.invalid_own_ids == []
    assert good.unspoken_foreign_ids == []
    assert good.valid_ids == ["ENG-901", "ARC-901"]


def test_verify_gate_verdict_repair_and_schema() -> None:
    """verify_and_repair_verdict strips unspoken IDs, repairs empty slots, calibrates confidence, and validates."""
    spoken_ids = {"ENG-901", "ARC-902", "HIS-903"}
    raw_verdict = {
        "run_id": "synthetic-run-001",
        "samenvatting": "The village mill slowed because a sluice valve was partially closed.",
        "oorzaak": {
            "ingreep": {
                "bewering": "Sluice valve adjusted on day 2.",
                "bewijs": ["ENG\u2011901", "ENG-901", "ENG-999"],
                "zekerheid": 0.99,
            },
            "mechanisme": {
                "bewering": "Reduced head pressure dropped wheel rpm.",
                "bewijs": ["ENG-901", "ARC-902", "HIS-903"],
                "zekerheid": 0.99,
            },
            "verloop": {
                "bewering": "Hallucinated claim with only unspoken evidence.",
                "bewijs": ["ORA-999"],
                "zekerheid": 0.85,
            },
        },
        "acties": [
            {
                "actie": "Inspect sluice pin before opening the main gate.",
                "reden": "Prevents sudden surge on the wooden paddles.",
                "bewijs": ["ENG-901", "GUA-999", "ENG-901"],
            }
        ],
        "risicos": [
            {
                "risico": "Opening the gate too fast damages the axle.",
                "mitigatie": "Open in 10% increments while monitoring rpm.",
            }
        ],
        "voorspelling": {
            "dag": 12,
            "bewering": "Unfounded prediction relying on unspoken ID.",
            "bewijs": ["BEA-999"],
        },
    }
    raw_geschillen = [
        {
            "onderwerp": "Axle wear vs low water flow",
            "standpunten": {
                "engineer": "Gear teeth are intact; water flow dropped.",
                "historian": "Previous repairs lasted five winters.",
            },
            "uitkomst": "Low water flow confirmed after checking logs.",
            "bewijs": ["ENG-901", "HIS\u2013903", "ARC-999"],
        },
        {
            "onderwerp": "Unverified rumor about weather vane",
            "standpunten": {
                "archmage": "Wind shifted north.",
                "oracle": "No vane anomaly observed.",
            },
            "uitkomst": "Should be cleared because no spoken ID supports it.",
            "bewijs": ["ORA-998"],
        },
    ]

    repaired_verdict, repaired_geschillen, warnings = verify_gate.verify_and_repair_verdict(
        raw_verdict, raw_geschillen, spoken_ids
    )

    assert len(warnings) >= 4
    # Deduplicated & stripped unspoken IDs, rare hyphens normalized
    assert repaired_verdict["oorzaak"]["ingreep"]["bewijs"] == ["ENG-901"]
    assert repaired_verdict["oorzaak"]["ingreep"]["zekerheid"] <= 0.75  # 1 role cap

    assert repaired_verdict["oorzaak"]["mechanisme"]["bewijs"] == ["ENG-901", "ARC-902", "HIS-903"]
    assert repaired_verdict["oorzaak"]["mechanisme"]["zekerheid"] <= 0.98  # 3 roles cap

    # Hallucinated slot nullified with 0.0 confidence
    assert repaired_verdict["oorzaak"]["verloop"]["bewering"] is None
    assert repaired_verdict["oorzaak"]["verloop"]["bewijs"] == []
    assert repaired_verdict["oorzaak"]["verloop"]["zekerheid"] == 0.0

    # Deduplicated acties bewijs & dropped invalid voorspelling
    assert repaired_verdict["acties"][0]["bewijs"] == ["ENG-901"]
    assert "voorspelling" not in repaired_verdict

    # Geschillen repairs: first keeps valid spoken IDs, second nullifies uitkomst
    assert repaired_geschillen[0]["bewijs"] == ["ENG-901", "HIS-903"]
    assert repaired_geschillen[0]["uitkomst"] is not None
    assert repaired_geschillen[1]["bewijs"] == []
    assert repaired_geschillen[1]["uitkomst"] is None

    # Schema validation on repaired verdict & trace containing repaired geschillen
    assert _schema_errors(repaired_verdict, VERDICT_SCHEMA) == []
    trace_payload = {
        "run_id": "synthetic-run-001",
        "raadsleden": ["engineer", "archmage", "historian"],
        "beurten": [
            {"nr": 1, "ronde": 1, "spreker": "koning", "soort": "routering", "aan": "engineer", "vraag": "Check logs", "reden": "Start"},
            {"nr": 2, "ronde": 1, "spreker": "engineer", "soort": "bericht", "tekst": "ENG-901 shows low flow."},
        ],
        "geschillen": repaired_geschillen,
    }
    assert _schema_errors(trace_payload, TRACE_SCHEMA) == []


def test_pydantic_models_strict_openai_schema() -> None:
    """RouteringDecision and VerdictSynthesis produce strict schemas and convert to valid payloads."""
    routering_schema = RouteringDecision.model_json_schema()
    verdict_schema = VerdictSynthesis.model_json_schema()

    assert routering_schema.get("additionalProperties") is False
    assert set(RouteringDecision.model_fields.keys()).issubset(set(routering_schema.get("required", [])))
    assert verdict_schema.get("additionalProperties") is False
    assert set(VerdictSynthesis.model_fields.keys()).issubset(set(verdict_schema.get("required", [])))

    decision = RouteringDecision(
        aan="engineer",
        vraag="Check gear ratios in your logbook.",
        reden="Need baseline mechanical measurements before checking river flow.",
        open_vragen=["Did the sluice gate change on day 2?"],
        werk_hypothese="Mechanical wear is less likely than reduced water supply.",
    )
    stop_decision = RouteringDecision(
        aan=None,
        vraag=None,
        reden="Three independent roles corroborated the root cause.",
        open_vragen=[],
        werk_hypothese="Sluice valve restriction caused the slowdown.",
    )
    assert decision.aan == "engineer"
    assert stop_decision.aan is None and stop_decision.vraag is None

    synth = VerdictSynthesis(
        koning_samenvatting_bericht="Consensus reached: sluice restriction slowed the mill wheel (ENG-901, ARC-902).",
        samenvatting="A closed sluice valve starved the waterwheel of flow.",
        oorzaak=OorzaakOut(
            ingreep=CauseSlotOut(
                bewering="The upper sluice valve was turned down by 40%.",
                bewijs=["ENG-901", "HIS-903"],
                zekerheid=0.9,
            ),
            mechanisme=CauseSlotOut(
                bewering="Lower water head dropped wheel torque below load threshold.",
                bewijs=["ENG-901", "ARC-902"],
                zekerheid=0.88,
            ),
            verloop=CauseSlotOut(
                bewering="Slowdown occurred daily between 04:00 and 05:00.",
                bewijs=["GUA-904", "ENG-901"],
                zekerheid=0.85,
            ),
        ),
        acties=[
            ActieOut(
                actie="Record baseline valve settings before adjusting.",
                reden="Preserve rollback state.",
                bewijs=["HIS-903"],
            ),
            ActieOut(
                actie="Restore sluice opening gradually in small steps.",
                reden="Avoid hydraulic shock on the paddles.",
                bewijs=["ENG-901"],
            ),
        ],
        risicos=[
            RisicoOut(
                risico="Sudden valve opening floods the lower trough.",
                mitigatie="Step opening by 10% every 15 minutes.",
            )
        ],
        voorspelling=VoorspellingOut(
            dag=15,
            bewering="Next slowdown window expected on day 15 at 04:00.",
            bewijs=["ENG-901", "GUA-904"],
        ),
        geschillen=[
            GeschilOut(
                onderwerp="Wheel bearing wear vs water head drop",
                standpunten=[
                    StandpuntItem(rol="engineer", standpunt="Bearings are clean; flow dropped."),
                    StandpuntItem(rol="guardian_keeper", standpunt="Night patrol heard creaking."),
                ],
                uitkomst="Flow drop was primary; creaking came from dry outer rim.",
                bewijs=["ENG-901", "GUA-904"],
            )
        ],
    )

    run_id = "synthetic-schema-run"
    verdict_dict = to_verdict_dict(run_id, synth)
    geschillen_list = to_trace_geschillen(synth.geschillen)

    assert _schema_errors(verdict_dict, VERDICT_SCHEMA) == []
    trace_dict = {
        "run_id": run_id,
        "raadsleden": ["engineer", "archmage", "historian", "guardian_keeper"],
        "beurten": [
            {"nr": 1, "ronde": 1, "spreker": "koning", "soort": "routering", "aan": "engineer", "vraag": "Check logs", "reden": "Start"},
            {"nr": 2, "ronde": 1, "spreker": "engineer", "soort": "bericht", "tekst": "ENG-901 checked."},
        ],
        "geschillen": geschillen_list,
    }
    assert _schema_errors(trace_dict, TRACE_SCHEMA) == []


def test_king_and_council_mocked_e2e() -> None:
    """End-to-end mocked KingOrchestrator + synthesize_verdict_and_geschillen contract test."""
    from king import KingOrchestrator, synthesize_verdict_and_geschillen

    leden = raadsleden.alle()
    run_id = "synthetic-e2e-001"

    scripted_decisions = [
        RouteringDecision(
            aan="engineer",
            vraag="Inspect your synthetic mill log for timing anomalies.",
            reden="Start with quantitative measurements.",
            open_vragen=["What changed on day 2?", "Which systems are affected?"],
            werk_hypothese="Initial probe of mechanical measurements.",
        ),
        RouteringDecision(
            aan="archmage",
            vraag="Cross-check ENG-901 against energy flow records.",
            reden="Verify whether upstream energy flow dropped concurrently.",
            open_vragen=["Does historical data show this cycle?"],
            werk_hypothese="Timing drift coincides with upstream flow reduction.",
        ),
        RouteringDecision(
            aan="historian",
            vraag="Check historical records for recovery precautions.",
            reden="Confirm safe remediation steps before making changes.",
            open_vragen=[],
            werk_hypothese="Upstream valve adjustment caused periodic slowdowns.",
        ),
        RouteringDecision(
            aan=None,
            vraag=None,
            reden="Three roles provided corroborated evidence; ready to synthesize.",
            open_vragen=[],
            werk_hypothese="Root cause and recovery plan are established.",
        ),
    ]
    decision_iter = iter(scripted_decisions)

    async def mock_router_invoke(schema_cls: type, system_prompt: str, user_prompt: str):
        assert schema_cls is RouteringDecision
        return next(decision_iter), {"input_token_count": 50, "output_token_count": 20}

    scripted_synthesis = VerdictSynthesis(
        koning_samenvatting_bericht=(
            "Council consensus: ENG-901, ARC-902, and HIS-903 show a valve timing mismatch."
        ),
        samenvatting="A secondary valve offset starved the clockwork mill on a 4-day cycle.",
        oorzaak=OorzaakOut(
            ingreep=CauseSlotOut(
                bewering="Secondary valve offset introduced on day 2.",
                bewijs=["ENG-901", "ARC-902"],
                zekerheid=0.90,
            ),
            mechanisme=CauseSlotOut(
                bewering="Offset pressure waves caused periodic stalling.",
                bewijs=["ENG-901", "ARC-902", "HIS-903"],
                zekerheid=0.95,
            ),
            verloop=CauseSlotOut(
                bewering="Incidents repeated every 4 days starting day 2.",
                bewijs=["ENG-901", "HIS-903"],
                zekerheid=0.88,
            ),
        ),
        acties=[
            ActieOut(
                actie="Back up calibration ledger before adjusting valves.",
                reden="Ensure safe rollback.",
                bewijs=["HIS-903"],
            ),
            ActieOut(
                actie="Re-synchronize valve offset in gradual 0.1% steps.",
                reden="Prevent sudden pressure surge.",
                bewijs=["ENG-901", "ARC-902"],
            ),
        ],
        risicos=[
            RisicoOut(
                risico="Abrupt shutdown damages mill gearing.",
                mitigatie="Use gradual stepped re-synchronization after backup.",
            )
        ],
        voorspelling=VoorspellingOut(
            dag=14,
            bewering="Next stall cycle predicted on day 14 if uncorrected.",
            bewijs=["ENG-901", "ARC-902"],
        ),
        geschillen=[
            GeschilOut(
                onderwerp="Immediate stop vs gradual recalibration",
                standpunten=[
                    StandpuntItem(rol="archmage", standpunt="Halt immediately to protect flow."),
                    StandpuntItem(rol="historian", standpunt="Gradual calibration after backup is required."),
                ],
                uitkomst="Adopt backup followed by gradual calibration.",
                bewijs=["HIS-903", "ENG-901"],
            )
        ],
    )

    async def mock_synth_invoke(schema_cls: type, system_prompt: str, user_prompt: str):
        assert schema_cls is VerdictSynthesis
        return scripted_synthesis, {"input_token_count": 120, "output_token_count": 80}

    mock_member_replies = {
        "engineer": ("ENG-901 confirms a valve offset introduced on day 2.", "mill_log.csv"),
        "archmage": ("ARC-902 shows upstream flow dipping in sync with ENG-901.", "flow_notes.md"),
        "historian": ("HIS-903 warns against abrupt halts and mandates backup first.", "chronicle.md"),
    }

    async def _run_e2e() -> tuple[dict, dict]:
        orchestrator = KingOrchestrator(leden=leden, invoke_structured=mock_router_invoke)
        beurten: list[dict] = []
        debat: list[str] = []
        spoken_ids: set[str] = set()
        ronde = 1

        while True:
            decision, _usage = await orchestrator.decide_next(beurten, debat, max_turns=10)
            beurten.append(
                {
                    "nr": len(beurten) + 1,
                    "ronde": ronde,
                    "spreker": "koning",
                    "soort": "routering",
                    "aan": decision.aan,
                    "vraag": decision.vraag,
                    "reden": decision.reden,
                    "open_vragen": decision.open_vragen,
                }
            )
            if decision.aan is None:
                break

            role = decision.aan
            reply_text, source_file = mock_member_replies[role]
            beurten.append(
                {
                    "nr": len(beurten) + 1,
                    "ronde": ronde,
                    "spreker": role,
                    "soort": "toolcall",
                    "tool": "zoek",
                    "argumenten": {"bestand": source_file},
                    "pad": f"https://mock/{role}/mcp",
                }
            )
            beurten.append(
                {
                    "nr": len(beurten) + 1,
                    "ronde": ronde,
                    "spreker": role,
                    "soort": "bericht",
                    "tekst": reply_text,
                }
            )
            spoken_ids.update(verify_gate.extract_ids(reply_text))
            debat.append(f"{role}: {reply_text}")
            ronde += 1

        closing_text, verdict, geschillen, _warnings, _synth_usage = (
            await synthesize_verdict_and_geschillen(
                None,
                run_id=run_id,
                debat=debat,
                beurten=beurten,
                leden=leden,
                spoken_member_ids=spoken_ids,
                invoke_structured=mock_synth_invoke,
            )
        )
        beurten.append(
            {
                "nr": len(beurten) + 1,
                "ronde": ronde + 1,
                "spreker": "koning",
                "soort": "bericht",
                "tekst": closing_text,
            }
        )

        trace = {
            "run_id": run_id,
            "raadsleden": [lid.ROL for lid in leden],
            "beurten": beurten,
            "geschillen": geschillen,
        }
        return trace, verdict

    trace, verdict = asyncio.run(_run_e2e())

    # Every member bericht is preceded by routering with matching aan, non-empty reden, and open_vragen list
    routering_turns = [b for b in trace["beurten"] if b["soort"] == "routering"]
    assert len(routering_turns) == 4
    assert routering_turns[-1]["aan"] is None
    assert routering_turns[-1]["vraag"] is None

    last_routing: dict | None = None
    for b in trace["beurten"]:
        if b["soort"] == "routering":
            assert isinstance(b["reden"], str) and b["reden"].strip()
            assert isinstance(b.get("open_vragen"), list)
            last_routing = b
        elif b["soort"] == "bericht" and b["spreker"] in EXPECTED_ROLES:
            assert last_routing is not None
            assert last_routing["aan"] == b["spreker"]
            last_routing = None

    assert validate.spelregels(trace) == []
    assert _schema_errors(trace, TRACE_SCHEMA) == []
    assert _schema_errors(verdict, VERDICT_SCHEMA) == []
    assert verdict["run_id"] == trace["run_id"]
    assert verdict["samenvatting"] != "TODO"


def test_king_hard_budget_cap_at_10() -> None:
    """KingOrchestrator stops deterministically at max_turns=10 with zero LLM calls."""
    leden = raadsleden.alle()
    role_cycle = [lid.ROL for lid in leden]

    async def fail_if_called(schema_cls: type, system_prompt: str, user_prompt: str):
        raise AssertionError("must not call LLM when budget exhausted")

    orchestrator = king.KingOrchestrator(leden=leden, invoke_structured=fail_if_called)
    orchestrator.open_vragen = ["Unclosed synthetic question"]
    orchestrator.werk_hypothese = "Sluice valve drift Hypothesis"

    beurten: list[dict] = [
        {"nr": 1, "ronde": 0, "spreker": "engineer", "soort": "bestand", "bestand": "engineer/mill.md"},
    ]
    debat: list[str] = []
    for i in range(10):
        role = role_cycle[i % len(role_cycle)]
        ronde = (i // len(role_cycle)) + 1
        beurten.append(
            {
                "nr": len(beurten) + 1,
                "ronde": ronde,
                "spreker": "koning",
                "soort": "routering",
                "aan": role,
                "vraag": f"Check synthetic turn {i + 1}",
                "reden": "Synthetic routing",
            }
        )
        beurten.append(
            {
                "nr": len(beurten) + 1,
                "ronde": ronde,
                "spreker": role,
                "soort": "toolcall",
                "tool": "lees_kennisbron",
                "argumenten": {},
                "pad": f"https://mock.local/{role}/mcp",
            }
        )
        beurten.append(
            {
                "nr": len(beurten) + 1,
                "ronde": ronde,
                "spreker": role,
                "soort": "bericht",
                "tekst": f"Synthetic statement {i + 1} citing ENG-901.",
            }
        )
        debat.append(f"{role}: Synthetic statement {i + 1} citing ENG-901.")

    # Non-member koning bericht must not confuse count either
    beurten.append(
        {"nr": len(beurten) + 1, "ronde": 3, "spreker": "koning", "soort": "bericht", "tekst": "Note"}
    )

    decision, usage = asyncio.run(orchestrator.decide_next(beurten, debat, max_turns=10))

    assert decision.aan is None
    assert decision.vraag is None
    assert decision.open_vragen == []
    assert "10" in decision.reden
    assert decision.werk_hypothese == "Sluice valve drift Hypothesis"
    assert orchestrator.open_vragen == []
    assert usage is None


def test_king_invalid_role_retry_and_fallback() -> None:
    """Invalid/inactive role triggers correction retry with merged usage, and safe fallback if retry also fails."""
    subset_roles = ("engineer", "archmage", "historian")
    subset_leden = [lid for lid in raadsleden.alle() if lid.ROL in subset_roles]
    assert len(subset_leden) == 3

    # Case 1: attempt 1 returns role outside active 3-member subset -> retry succeeds & merges usage
    retry_prompts: list[str] = []
    attempt1_responses = iter(
        [
            (
                RouteringDecision(
                    aan="oracle",  # valid globally, NOT in 3-member subset
                    vraag="Check ORA\u2011906 logs.",
                    reden="Outside active subset.",
                    open_vragen=["Check sluice?"],
                    werk_hypothese="Initial guess.",
                ),
                {"input_token_count": 40, "output_token_count": 10, "total_token_count": 50},
            ),
            (
                RouteringDecision(
                    aan="archmage",
                    vraag="Inspect ARC\u2011902 pressure gauge.",
                    reden="Verify upstream pressure drop.",
                    open_vragen=["Did head drop on day 2?"],
                    werk_hypothese="Upstream pressure dropped.",
                ),
                {"input_token_count": 60, "output_token_count": 15, "total_token_count": 75},
            ),
        ]
    )

    async def mock_retry_ok(schema_cls: type, system_prompt: str, user_prompt: str):
        assert schema_cls is RouteringDecision
        retry_prompts.append(user_prompt)
        return next(attempt1_responses)

    orch_ok = king.KingOrchestrator(leden=subset_leden, invoke_structured=mock_retry_ok)
    decision_ok, usage_ok = asyncio.run(orch_ok.decide_next([], [], max_turns=10))

    assert len(retry_prompts) == 2
    assert "CORRECTION" in retry_prompts[1]
    assert "oracle" in retry_prompts[1]
    assert decision_ok.aan == "archmage"
    assert decision_ok.vraag == "Inspect ARC-902 pressure gauge."
    assert not validate.RARE_STREEP.search(decision_ok.vraag or "")
    assert usage_ok == {
        "input_token_count": 100,
        "output_token_count": 25,
        "total_token_count": 125,
    }

    # Case 2: BOTH attempts return invalid roles -> _sanitize_decision falls back to unspoken active role
    both_bad_calls = 0
    both_bad_responses = iter(
        [
            (
                RouteringDecision(
                    aan="beast_master",  # not in subset
                    vraag="",
                    reden="",
                    open_vragen=["  ", "Check ENG\u2013901"],
                    werk_hypothese="Drift on ENG\u2014901",
                ),
                {"input_token_count": 30, "output_token_count": 12},
            ),
            (
                RouteringDecision.model_construct(
                    aan="ghost_advisor",  # completely invalid role
                    vraag="   ",
                    reden="   ",
                    open_vragen=["Follow up on ENG\u2011901"],
                    werk_hypothese="Check ENG\u2011901",
                ),
                {"input_token_count": 45, "output_token_count": 18},
            ),
        ]
    )

    async def mock_both_bad(schema_cls: type, system_prompt: str, user_prompt: str):
        nonlocal both_bad_calls
        both_bad_calls += 1
        return next(both_bad_responses)

    beurten_one_spoken = [
        {"nr": 1, "ronde": 1, "spreker": "engineer", "soort": "bericht", "tekst": "ENG-901 reported."}
    ]
    orch_fallback = king.KingOrchestrator(leden=subset_leden, invoke_structured=mock_both_bad)
    decision_fb, usage_fb = asyncio.run(
        orch_fallback.decide_next(beurten_one_spoken, ["engineer: ENG-901 reported."], max_turns=10)
    )

    assert both_bad_calls == 2
    assert decision_fb.aan in set(subset_roles)
    assert decision_fb.aan != "engineer"  # must pick an unspoken active role
    assert decision_fb.vraag and decision_fb.vraag.strip()
    assert decision_fb.reden and decision_fb.reden.strip()
    assert decision_fb.open_vragen == ["Follow up on ENG-901"]
    assert decision_fb.werk_hypothese == "Check ENG-901"
    assert usage_fb == {"input_token_count": 75, "output_token_count": 30}

    # Case 3: All active roles already spoken and both attempts invalid -> picks least-spoken active role
    beurten_all_spoken = [
        {"nr": 1, "ronde": 1, "spreker": "engineer", "soort": "bericht", "tekst": "ENG-901"},
        {"nr": 2, "ronde": 1, "spreker": "engineer", "soort": "bericht", "tekst": "ENG-902"},
        {"nr": 3, "ronde": 1, "spreker": "archmage", "soort": "bericht", "tekst": "ARC-901"},
        {"nr": 4, "ronde": 1, "spreker": "archmage", "soort": "bericht", "tekst": "ARC-902"},
        {"nr": 5, "ronde": 1, "spreker": "historian", "soort": "bericht", "tekst": "HIS-903"},
    ]

    async def mock_always_invalid(schema_cls: type, system_prompt: str, user_prompt: str):
        return (
            RouteringDecision.model_construct(
                aan="invalid_role",
                vraag="Check logs.",
                reden="Still invalid.",
                open_vragen=[],
                werk_hypothese="Working.",
            ),
            {"input_token_count": 10, "output_token_count": 5},
        )

    orch_least = king.KingOrchestrator(leden=subset_leden, invoke_structured=mock_always_invalid)
    decision_least, usage_least = asyncio.run(
        orch_least.decide_next(beurten_all_spoken, [], max_turns=10)
    )
    assert decision_least.aan == "historian"
    assert usage_least == {"input_token_count": 20, "output_token_count": 10}


def test_king_parse_structured_strict_fallback_and_fenced_json() -> None:
    """_parse_structured falls back from strict schema failure to fenced JSON and merges usage."""
    valid_payload = {
        "aan": "engineer",
        "vraag": "Verify waterwheel gear ratio in ENG-901.",
        "reden": "Need baseline mechanical readings.",
        "open_vragen": ["Did the sluice pin shift?"],
        "werk_hypothese": "Gear slip caused by reduced head pressure.",
    }
    fenced_json = f"```json\n{json.dumps(valid_payload, indent=2)}\n```"

    # Subtest A: 1st call raises RuntimeError("strict schema rejected"), 2nd call returns fenced JSON
    calls_a: list[tuple[str, dict | None]] = []

    class _FakeAgentRaiseThenFence:
        async def run(self, prompt: str, options: dict | None = None):
            calls_a.append((prompt, options))
            if len(calls_a) == 1:
                raise RuntimeError("strict schema rejected")
            return SimpleNamespace(
                value=None,
                text=fenced_json,
                usage_details={
                    "input_token_count": 90,
                    "output_token_count": 35,
                    "cache_read_input_token_count": 20,
                },
            )

    parsed_a, usage_a = asyncio.run(
        king._parse_structured(
            _FakeAgentRaiseThenFence(),
            RouteringDecision,
            "system prompt",
            "user prompt",
        )
    )
    assert len(calls_a) == 2
    assert calls_a[0][1] == {"response_format": RouteringDecision}
    assert calls_a[1][1] == {"response_format": {"type": "json_object"}}
    assert isinstance(parsed_a, RouteringDecision)
    assert parsed_a.aan == "engineer"
    assert parsed_a.vraag == "Verify waterwheel gear ratio in ENG-901."
    assert usage_a == {
        "input_token_count": 90,
        "output_token_count": 35,
        "cache_read_input_token_count": 20,
    }

    # Subtest B: 1st call returns unparseable text WITH usage_details, 2nd call returns fenced JSON -> usage merged
    calls_b: list[dict | None] = []

    class _FakeAgentUnparseableThenFence:
        async def run(self, prompt: str, options: dict | None = None):
            calls_b.append(options)
            if len(calls_b) == 1:
                return SimpleNamespace(
                    value=None,
                    text="Sorry, I cannot comply with strict schema.",
                    usage_details={
                        "input_token_count": 50,
                        "output_token_count": 15,
                        "total_token_count": 65,
                        "cache_read_input_token_count": 10,
                    },
                )
            return SimpleNamespace(
                value=None,
                text=f"Here is the JSON:\n{fenced_json}",
                usage_details={
                    "input_token_count": 80,
                    "output_token_count": 30,
                    "total_token_count": 110,
                    "cache_read_input_token_count": 25,
                },
            )

    parsed_b, usage_b = asyncio.run(
        king._parse_structured(
            _FakeAgentUnparseableThenFence(),
            RouteringDecision,
            "system prompt",
            "user prompt",
            options={"reasoning_effort": "low"},
        )
    )
    assert len(calls_b) == 2
    assert calls_b[0] == {"reasoning_effort": "low", "response_format": RouteringDecision}
    assert calls_b[1] == {"reasoning_effort": "low", "response_format": {"type": "json_object"}}
    assert isinstance(parsed_b, RouteringDecision)
    assert parsed_b.aan == "engineer"
    assert usage_b == {
        "input_token_count": 130,
        "output_token_count": 45,
        "total_token_count": 175,
        "cache_read_input_token_count": 35,
    }


def test_king_synthesis_repairs_empty_and_todo_outputs() -> None:
    """synthesize_verdict_and_geschillen repairs TODO summary, empty acties/risicos, unspoken IDs, and rare hyphens."""
    leden = raadsleden.alle()
    broken_synth = VerdictSynthesis(
        koning_samenvatting_bericht=(
            "Council consensus: sluice valve offset proven by ENG\u2011901 and ARC\u2013902."
        ),
        samenvatting="TODO",
        oorzaak=OorzaakOut(
            ingreep=CauseSlotOut(
                bewering="Sluice valve shifted per ENG\u2011901.",
                bewijs=["ENG\u2011901", "ORA-999"],
                zekerheid=0.95,
            ),
            mechanisme=CauseSlotOut(
                bewering="Head pressure dropped per ARC\u2013902 and ENG\u2014901.",
                bewijs=["ARC\u2013902", "ENG\u2014901", "BEA-998"],
                zekerheid=0.90,
            ),
            verloop=CauseSlotOut(
                bewering="Unfounded timeline relying only on unspoken ID.",
                bewijs=["GUA-999"],
                zekerheid=0.80,
            ),
        ),
        acties=[],
        risicos=[],
        voorspelling=VoorspellingOut(
            dag=15,
            bewering="Unbacked prediction.",
            bewijs=["ORA-999"],
        ),
        geschillen=[
            GeschilOut(
                onderwerp="Valve offset vs gear wear (ENG\u2011901)",
                standpunten=[
                    StandpuntItem(rol="engineer", standpunt="ENG\u2011901 shows valve offset."),
                    StandpuntItem(rol="archmage", standpunt="ARC\u2013902 confirms head drop."),
                ],
                uitkomst="Confirmed valve offset via ENG\u2011901.",
                bewijs=["ENG\u2011901", "ORA-999"],
            )
        ],
    )

    async def mock_synth(schema_cls: type, system_prompt: str, user_prompt: str):
        assert schema_cls is VerdictSynthesis
        return broken_synth, {"input_token_count": 100, "output_token_count": 60}

    closing_text, verdict, geschillen, warnings, usage = asyncio.run(
        king.synthesize_verdict_and_geschillen(
            None,
            run_id="synthetic-repair-001",
            debat=[
                "engineer: ENG-901 shows sluice valve offset.",
                "archmage: ARC-902 shows head pressure drop.",
            ],
            beurten=[
                {"nr": 1, "ronde": 1, "spreker": "engineer", "soort": "bericht", "tekst": "ENG-901"},
                {"nr": 2, "ronde": 1, "spreker": "archmage", "soort": "bericht", "tekst": "ARC-902"},
            ],
            leden=leden,
            spoken_member_ids={"ENG-901", "ARC-902"},
            invoke_structured=mock_synth,
        )
    )

    assert verdict["samenvatting"] != "TODO"
    assert verdict["samenvatting"].strip() != ""
    assert len(verdict["acties"]) >= 1
    assert len(verdict["risicos"]) >= 1
    assert "voorspelling" not in verdict

    # Unspoken IDs stripped, rare hyphens normalized everywhere
    assert verdict["oorzaak"]["ingreep"]["bewijs"] == ["ENG-901"]
    assert verdict["oorzaak"]["mechanisme"]["bewijs"] == ["ARC-902", "ENG-901"]
    assert verdict["oorzaak"]["verloop"]["bewering"] is None
    assert verdict["oorzaak"]["verloop"]["bewijs"] == []
    assert verdict["oorzaak"]["verloop"]["zekerheid"] == 0.0
    assert geschillen[0]["bewijs"] == ["ENG-901"]

    serialized_verdict = json.dumps(verdict, ensure_ascii=False)
    serialized_geschillen = json.dumps(geschillen, ensure_ascii=False)
    assert not validate.RARE_STREEP.search(closing_text)
    assert not validate.RARE_STREEP.search(serialized_verdict)
    assert not validate.RARE_STREEP.search(serialized_geschillen)
    assert "ORA-999" not in serialized_verdict
    assert "BEA-998" not in serialized_verdict
    assert "GUA-999" not in serialized_verdict

    assert len(warnings) >= 4
    assert usage == {"input_token_count": 100, "output_token_count": 60}
    assert _schema_errors(verdict, VERDICT_SCHEMA) == []


def test_verify_gate_unresolved_geschil_dampens_zekerheid() -> None:
    """An unresolved geschil (uitkomst=None) involving a supporting role dampens slot zekerheid to <= 0.75."""
    spoken_ids = {"ENG-901", "ARC-902", "HIS-903", "GUA-904", "ORA-906"}

    def _make_verdict() -> dict:
        return {
            "run_id": "synthetic-geschil-001",
            "samenvatting": "Three roles corroborated the sluice mechanism.",
            "oorzaak": {
                "ingreep": {
                    "bewering": "Sluice valve adjusted on day 2.",
                    "bewijs": ["ENG-901", "ARC-902", "HIS-903"],
                    "zekerheid": 0.95,
                },
                "mechanisme": {
                    "bewering": "Reduced water head dropped wheel torque.",
                    "bewijs": ["ENG-901", "ARC-902", "HIS-903"],
                    "zekerheid": 0.95,
                },
                "verloop": {
                    "bewering": "Slowdowns recurred every four days.",
                    "bewijs": ["ENG-901", "HIS-903"],
                    "zekerheid": 0.90,
                },
            },
            "acties": [
                {
                    "actie": "Log baseline settings before opening the valve.",
                    "reden": "Preserves rollback state.",
                    "bewijs": ["HIS-903"],
                }
            ],
            "risicos": [
                {
                    "risico": "Rapid valve opening stresses wooden paddles.",
                    "mitigatie": "Step opening by 10% increments.",
                }
            ],
        }

    # Case 1: All geschillen resolved -> 3-role slot keeps zekerheid == 0.95
    resolved_geschillen = [
        {
            "onderwerp": "Gear wear vs low water flow",
            "standpunten": {
                "engineer": "Gear teeth are intact; water head dropped.",
                "historian": "Chronicle notes prior bearing wear.",
            },
            "uitkomst": "Water head drop confirmed as primary cause.",
            "bewijs": ["ENG-901", "HIS-903"],
        }
    ]
    verdict_resolved, geschillen_resolved, _ = verify_gate.verify_and_repair_verdict(
        _make_verdict(), resolved_geschillen, spoken_ids
    )
    assert geschillen_resolved[0]["uitkomst"] is not None
    assert verdict_resolved["oorzaak"]["mechanisme"]["zekerheid"] == 0.95
    assert verdict_resolved["oorzaak"]["ingreep"]["zekerheid"] == 0.95

    # Case 2: Unresolved geschil (uitkomst=None) involving one of the supporting roles -> zekerheid <= 0.75
    unresolved_overlapping = [
        {
            "onderwerp": "Whether secondary bypass gate was also opened",
            "standpunten": {
                "engineer": "Bypass gate remained shut.",
                "guardian_keeper": "Night watch saw a lantern near the bypass gate.",
            },
            "uitkomst": None,
            "bewijs": [],
        }
    ]
    verdict_dampened, geschillen_open, _ = verify_gate.verify_and_repair_verdict(
        _make_verdict(), unresolved_overlapping, spoken_ids
    )
    assert geschillen_open[0]["uitkomst"] is None
    assert verdict_dampened["oorzaak"]["mechanisme"]["zekerheid"] <= 0.75
    assert verdict_dampened["oorzaak"]["ingreep"]["zekerheid"] <= 0.75
    assert verdict_dampened["oorzaak"]["verloop"]["zekerheid"] <= 0.75

    # Case 3: Unresolved geschil involving ONLY unrelated roles -> 3-role slot remains 0.95
    unresolved_unrelated = [
        {
            "onderwerp": "Weather vane alignment",
            "standpunten": {
                "guardian_keeper": "Vane pointed east.",
                "oracle": "Vane pointed northeast.",
            },
            "uitkomst": None,
            "bewijs": [],
        }
    ]
    verdict_unrelated, _, _ = verify_gate.verify_and_repair_verdict(
        _make_verdict(), unresolved_unrelated, spoken_ids
    )
    assert verdict_unrelated["oorzaak"]["mechanisme"]["zekerheid"] == 0.95


def test_verify_gate_id_extraction_quirks() -> None:
    """extract_ids handles punctuation, CRLF, Unicode dashes, deduping, and rejects invalid IDs."""
    sample = (
        "Bracketed [ENG-901], parenthesized (ARC-902), trailing period HIS-903.\r\n"
        "Comma GUA-904, semicolon BEA\u2014905; colon ORA\u2212906:\r\n"
        "Duplicates in different forms: ENG\u2011901, ARC\u2013902, HIS-903.\r\n"
        "Invalid tokens: ENG-12, ENG-1234, ZZZ-901, eng-901, ENG-90A, XENG-907, ENG-908X."
    )
    extracted = verify_gate.extract_ids(sample)
    assert extracted == ["ENG-901", "ARC-902", "HIS-903", "GUA-904", "BEA-905", "ORA-906"]

    # Order preservation across every Unicode dash variant
    dash_line = " ".join(
        f"ENG{dash}90{idx}" for idx, dash in enumerate(UNICODE_DASHES, start=1)
    )
    assert verify_gate.extract_ids(dash_line) == [
        f"ENG-90{idx}" for idx in range(1, len(UNICODE_DASHES) + 1)
    ]


def test_council_tool_results_all_payload_shapes() -> None:
    """council.tool_results flattens every supported function_result payload shape cleanly."""
    response = SimpleNamespace(
        messages=[
            SimpleNamespace(
                contents=[
                    # Non-function_result entry must be ignored
                    SimpleNamespace(type="function_call", name="lees_kennisbron", arguments="{}"),
                    # Plain string and whitespace-only string
                    SimpleNamespace(type="function_result", result="ENG-901 plain string"),
                    SimpleNamespace(type="function_result", result="   \n\t  "),
                    # list[str]
                    SimpleNamespace(type="function_result", result=["ARC-902 first", "   ", "ARC-903 second"]),
                    # list[dict] (with text key and without text key)
                    SimpleNamespace(
                        type="function_result",
                        result=[
                            {"text": "HIS-903 from dict text"},
                            {"id": "GUA-904", "status": "ok"},
                        ],
                    ),
                    # list[Content-like obj with .text]
                    SimpleNamespace(
                        type="function_result",
                        result=[
                            SimpleNamespace(text="BEA-905 from content obj"),
                            SimpleNamespace(text="   "),
                        ],
                    ),
                    # Single dict (with text key and without text key)
                    SimpleNamespace(type="function_result", result={"text": "ORA-906 single dict"}),
                    SimpleNamespace(type="function_result", result={"record": "ENG-907", "rpm": 12}),
                    # items fallback when result is None
                    SimpleNamespace(
                        type="function_result",
                        result=None,
                        items=[SimpleNamespace(text="HIS-908 from items fallback")],
                    ),
                    # Both result and items None
                    SimpleNamespace(type="function_result", result=None, items=None),
                ]
            )
        ]
    )

    extracted = council.tool_results(response)
    assert "ENG-901 plain string" in extracted
    assert "ARC-902 first" in extracted
    assert "ARC-903 second" in extracted
    assert "HIS-903 from dict text" in extracted
    assert any("GUA-904" in item for item in extracted)
    assert "BEA-905 from content obj" in extracted
    assert "ORA-906 single dict" in extracted
    assert any("ENG-907" in item for item in extracted)
    assert "HIS-908 from items fallback" in extracted
    assert len(extracted) == 9

    # Empty / None messages response
    assert council.tool_results(SimpleNamespace(messages=None)) == []
    assert council.tool_results(SimpleNamespace(messages=[])) == []

    # Regression: MCP JSON-encoded envelope with literal \\n before CSV record IDs
    json_envelope = '{"result": "id,day\\nENG-901,1\\nENG-902,2"}'
    env_resp = SimpleNamespace(
        messages=[SimpleNamespace(contents=[SimpleNamespace(type="function_result", result=json_envelope)])]
    )
    unwrapped = council.tool_results(env_resp)
    assert unwrapped == ["id,day\nENG-901,1\nENG-902,2"]
    assert verify_gate.extract_ids_from_text_set(unwrapped) == {"ENG-901", "ENG-902"}
    assert verify_gate.extract_ids_from_text_set([json_envelope]) == {"ENG-901", "ENG-902"}


def test_council_instructies_modes_and_language(tmp_path: Path) -> None:
    """council.instructies renders MCP mode, local fallback mode, no-source mode, BELANG, and language."""
    lid_with_belang = SimpleNamespace(
        ROL="engineer",
        NAAM="Synthetic Millwright",
        PERSPECTIEF="Monitors waterwheel rpm and gear ratios.",
        BELANG="Prefers mechanical explanations over sluice policy changes.",
    )
    lid_without_belang = SimpleNamespace(
        ROL="historian",
        NAAM="Synthetic Archivist",
        PERSPECTIEF="Maintains mill repair ledgers.",
    )

    # MCP mode (English vs Dutch)
    mcp_en = council.instructies(
        lid_with_belang,
        bestanden=[],
        mcp="https://mock.local/engineer/mcp",
        language="English",
    )
    assert "lees_kennisbron()" in mcp_en
    assert "zoek_betekenis" in mcp_en and "unavailable" in mcp_en
    assert "Prompt-injection guard" in mcp_en
    assert lid_with_belang.BELANG in mcp_en
    assert "Respond in English" in mcp_en

    mcp_nl = council.instructies(
        lid_without_belang,
        bestanden=[],
        mcp="https://mock.local/historian/mcp",
        language="Dutch",
    )
    assert "lees_kennisbron()" in mcp_nl
    assert "Your institutional interest (Belang):" not in mcp_nl
    assert "Respond in Dutch" in mcp_nl

    # Local fallback mode (embeds synthetic file contents)
    synthetic_file = tmp_path / "mill_log.md"
    synthetic_file.write_text("ENG-901 | day 2 | Wheel rpm dropped from 24 to 14.", encoding="utf-8")
    local_prompt = council.instructies(
        lid_with_belang,
        bestanden=[synthetic_file],
        mcp="",
        language="English",
    )
    assert "## mill_log.md" in local_prompt
    assert "ENG-901 | day 2 | Wheel rpm dropped from 24 to 14." in local_prompt
    assert "lees_kennisbron()" not in local_prompt

    # No-source mode
    no_source_prompt = council.instructies(
        lid_with_belang,
        bestanden=[],
        mcp="",
        language="English",
    )
    assert "no knowledge source available" in no_source_prompt
    assert "lees_kennisbron()" not in no_source_prompt


def test_council_tel_accumulates_usage() -> None:
    """council.tel ignores empty/None usage and accurately accumulates partial and cached tokens."""
    verbruik: dict[str, dict[str, int]] = {}

    council.tel(verbruik, "klein", None)
    council.tel(verbruik, "klein", {})
    assert verbruik == {}

    council.tel(verbruik, "klein", {"input_token_count": 120})
    council.tel(
        verbruik,
        "klein",
        {
            "input_token_count": 200,
            "cache_read_input_token_count": 80,
            "output_token_count": 45,
        },
    )
    council.tel(
        verbruik,
        "klein",
        {
            "input_token_count": None,
            "cache_read_input_token_count": None,
            "output_token_count": 30,
        },
    )
    council.tel(
        verbruik,
        "groot",
        {
            "input_token_count": 500,
            "cache_read_input_token_count": 150,
            "output_token_count": 90,
        },
    )

    assert verbruik["klein"] == {
        "beurten": 3,
        "invoer": 320,
        "gecached": 80,
        "uitvoer": 75,
    }
    assert verbruik["groot"] == {
        "beurten": 1,
        "invoer": 500,
        "gecached": 150,
        "uitvoer": 90,
    }


def test_council_member_retry_on_hallucinated_id_and_skipped_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """council.run() silently retries skipped MCP consultation and hallucinated own IDs, keeping trace clean."""
    monkeypatch.setattr(council, "ROOT", tmp_path)
    monkeypatch.setattr(council, "KENNISBRONNEN", tmp_path / "kennisbronnen")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://mock.local/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setenv("MODEL_KLEIN", "synthetic-small")
    monkeypatch.setenv("MODEL_GROOT", "synthetic-large")
    monkeypatch.delenv("COUNCIL_DEV_CHEAP", raising=False)
    monkeypatch.delenv("REASONING", raising=False)
    monkeypatch.setenv("COUNCIL_LANGUAGE", "English")
    for role in council.ROLLEN:
        monkeypatch.setenv(f"MCP_{role.upper()}", f"https://mock.local/{role}/mcp")

    monkeypatch.setattr(council, "client", lambda model_env: SimpleNamespace(model_env=model_env))

    class _FakeMCPTool:
        def __init__(self, name: str, url: str) -> None:
            self.name = name
            self.url = url

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(council, "MCPStreamableHTTPTool", _FakeMCPTool)

    member_prompts: dict[str, list[str]] = {"engineer": [], "archmage": []}

    class _FakeAgent:
        def __init__(self, chat_client, instructions, *, name: str = "", tools=None) -> None:
            self.name = name
            self.instructions = instructions
            self.tools = tools or []

        async def run(self, prompt: str, options=None):
            prompts = member_prompts.setdefault(self.name, [])
            prompts.append(prompt)
            call_nr = len(prompts)

            if self.name == "engineer":
                if call_nr == 1:
                    # Turn 1 initial: skips MCP tool completely -> must trigger silent retry
                    return SimpleNamespace(
                        text="The waterwheel slowed down on day 2 without checking logs.",
                        messages=[],
                        usage_details={"input_token_count": 30, "output_token_count": 10},
                    )
                # Turn 1 retry: calls lees_kennisbron and cites verified ENG-901
                return SimpleNamespace(
                    text="ENG-901 confirms a 40% sluice valve offset on day 2.",
                    messages=[
                        SimpleNamespace(
                            contents=[
                                SimpleNamespace(
                                    type="function_call",
                                    name="lees_kennisbron",
                                    arguments="{}",
                                ),
                                SimpleNamespace(
                                    type="function_result",
                                    result="ENG-901 | day 2 | Sluice valve offset measured at 40%.",
                                ),
                            ]
                        )
                    ],
                    usage_details={"input_token_count": 50, "output_token_count": 20},
                )

            if self.name == "archmage":
                if call_nr == 1:
                    # Turn 2 initial: calls lees_kennisbron, but cites hallucinated own ID ARC-999
                    return SimpleNamespace(
                        text="ARC-999 shows pressure collapse matching ENG-901.",
                        messages=[
                            SimpleNamespace(
                                contents=[
                                    SimpleNamespace(
                                        type="function_call",
                                        name="lees_kennisbron",
                                        arguments="{}",
                                    ),
                                    SimpleNamespace(
                                        type="function_result",
                                        result="ARC-902 | day 2 | Upstream head pressure dropped 35%.",
                                    ),
                                ]
                            )
                        ],
                        usage_details={"input_token_count": 40, "output_token_count": 15},
                    )
                # Turn 2 retry: returns valid ARC-902 with rare hyphen \u2011 and spoken ENG-901
                return SimpleNamespace(
                    text="ARC\u2011902 confirms upstream head pressure dropped 35% alongside ENG-901.",
                    messages=[],
                    usage_details={"input_token_count": 55, "output_token_count": 22},
                )

            raise AssertionError(f"Unexpected member invocation: {self.name}")

    monkeypatch.setattr(council, "Agent", _FakeAgent)

    routing_iter = iter(
        [
            RouteringDecision(
                aan="engineer",
                vraag="Inspect mill logs for day-2 mechanical changes.",
                reden="Start with mechanical measurements.",
                open_vragen=["Did upstream flow drop simultaneously?"],
                werk_hypothese="Initial mechanical probe.",
            ),
            RouteringDecision(
                aan="archmage",
                vraag="Cross-check ENG-901 against upstream head pressure records.",
                reden="Verify flow correlation.",
                open_vragen=[],
                werk_hypothese="Sluice offset reduced upstream head pressure.",
            ),
            RouteringDecision(
                aan=None,
                vraag=None,
                reden="Both key roles corroborated valve offset and head drop.",
                open_vragen=[],
                werk_hypothese="Sluice valve offset caused mill slowdown.",
            ),
        ]
    )

    async def mock_router_invoke(schema_cls: type, system_prompt: str, user_prompt: str):
        assert schema_cls is RouteringDecision
        return next(routing_iter), {"input_token_count": 60, "output_token_count": 20}

    def _make_orchestrator(llm_client, leden, *, language="English", options=None):
        return king.KingOrchestrator(
            llm_client,
            leden,
            language=language,
            options=options,
            invoke_structured=mock_router_invoke,
        )

    monkeypatch.setattr(council, "KingOrchestrator", _make_orchestrator)

    scripted_verdict = VerdictSynthesis(
        koning_samenvatting_bericht="Consensus: sluice offset caused head drop (ENG-901, ARC-902).",
        samenvatting="A 40% sluice valve offset reduced head pressure and slowed the mill wheel.",
        oorzaak=OorzaakOut(
            ingreep=CauseSlotOut(
                bewering="Sluice valve was turned down by 40% on day 2.",
                bewijs=["ENG-901"],
                zekerheid=0.75,
            ),
            mechanisme=CauseSlotOut(
                bewering="Lower water head reduced paddle torque by 35%.",
                bewijs=["ENG-901", "ARC-902"],
                zekerheid=0.90,
            ),
            verloop=CauseSlotOut(
                bewering="Slowdown began on day 2 immediately after valve offset.",
                bewijs=["ENG-901", "ARC-902"],
                zekerheid=0.88,
            ),
        ),
        acties=[
            ActieOut(
                actie="Restore sluice valve in 10% increments.",
                reden="Prevents surge on wooden paddles.",
                bewijs=["ENG-901", "ARC-902"],
            )
        ],
        risicos=[
            RisicoOut(
                risico="Abrupt opening may crack the outer rim.",
                mitigatie="Monitor rpm after each 10% step.",
            )
        ],
        voorspelling=None,
        geschillen=[],
    )

    async def mock_synth_invoke(schema_cls: type, system_prompt: str, user_prompt: str):
        assert schema_cls is VerdictSynthesis
        return scripted_verdict, {"input_token_count": 110, "output_token_count": 70}

    async def _mock_synthesize(llm_client, **kwargs):
        return await king.synthesize_verdict_and_geschillen(
            llm_client,
            invoke_structured=mock_synth_invoke,
            **kwargs,
        )

    monkeypatch.setattr(council, "synthesize_verdict_and_geschillen", _mock_synthesize)

    asyncio.run(council.run())

    trace_path = tmp_path / "trace.json"
    verdict_path = tmp_path / "verdict.json"
    assert trace_path.exists()
    assert verdict_path.exists()

    trace_text = trace_path.read_text(encoding="utf-8")
    verdict_text = verdict_path.read_text(encoding="utf-8")
    trace = json.loads(trace_text)
    verdict = json.loads(verdict_text)

    # Silent retries fired exactly once per offending turn with specific corrective feedback
    assert len(member_prompts["engineer"]) == 2
    assert "lees_kennisbron" in member_prompts["engineer"][1]
    assert len(member_prompts["archmage"]) == 2
    assert "ARC-999" in member_prompts["archmage"][1]

    # Trace records ONLY 1 bericht per member turn, with normalized hyphens and no hallucinated ID
    eng_berichten = [
        b for b in trace["beurten"] if b["soort"] == "bericht" and b["spreker"] == "engineer"
    ]
    arc_berichten = [
        b for b in trace["beurten"] if b["soort"] == "bericht" and b["spreker"] == "archmage"
    ]
    assert len(eng_berichten) == 1
    assert len(arc_berichten) == 1
    assert "ENG-901" in eng_berichten[0]["tekst"]
    assert "ARC-902" in arc_berichten[0]["tekst"]
    assert "ARC-999" not in arc_berichten[0]["tekst"]
    assert "\u2011" not in arc_berichten[0]["tekst"]

    # Both MCP lees_kennisbron toolcalls recorded
    toolcall_speakers = [
        (b["spreker"], b["tool"]) for b in trace["beurten"] if b["soort"] == "toolcall"
    ]
    assert ("engineer", "lees_kennisbron") in toolcall_speakers
    assert ("archmage", "lees_kennisbron") in toolcall_speakers

    # Full validation contract
    assert trace["status"] == "klaar"
    assert trace["aan_het_woord"] is None
    assert verdict["run_id"] == trace["run_id"]
    assert validate.spelregels(trace) == []
    assert _schema_errors(trace, TRACE_SCHEMA) == []
    assert _schema_errors(verdict, VERDICT_SCHEMA) == []
    assert not validate.RARE_STREEP.search(trace_text)
    assert not validate.RARE_STREEP.search(verdict_text)


def test_validate_catches_rule_violations() -> None:
    """validate.spelregels flags >10 member turns and unrouted member bericht; RARE_STREEP catches Unicode dashes."""
    roles = list(validate.ROLLEN)

    # (a) 11 member turns exceeds BEURTENBUDGET (10), even with valid routering before each turn
    beurten_11: list[dict] = []
    for i in range(11):
        role = roles[i % len(roles)]
        beurten_11.append(
            {
                "nr": len(beurten_11) + 1,
                "ronde": (i // len(roles)) + 1,
                "spreker": "koning",
                "soort": "routering",
                "aan": role,
                "vraag": "Synthetic check",
                "reden": "Synthetic routing",
            }
        )
        beurten_11.append(
            {
                "nr": len(beurten_11) + 1,
                "ronde": (i // len(roles)) + 1,
                "spreker": role,
                "soort": "bericht",
                "tekst": f"Turn {i + 1} citing ENG-901.",
            }
        )
    budget_errors = validate.spelregels({"beurten": beurten_11})
    assert len(budget_errors) == 1
    assert "11" in budget_errors[0] and "10" in budget_errors[0]

    # (b) Member bericht missing preceding routering to that exact role
    trace_wrong_role = {
        "beurten": [
            {
                "nr": 1,
                "ronde": 1,
                "spreker": "koning",
                "soort": "routering",
                "aan": "engineer",
                "vraag": "Check mill",
                "reden": "Start",
            },
            {
                "nr": 2,
                "ronde": 1,
                "spreker": "archmage",  # mismatch: routed to engineer
                "soort": "bericht",
                "tekst": "ARC-902 spoken out of turn.",
            },
        ]
    }
    wrong_role_errors = validate.spelregels(trace_wrong_role)
    assert len(wrong_role_errors) == 1
    assert "archmage" in wrong_role_errors[0]

    # Missing routering altogether & double bericht after one routering
    trace_double_bericht = {
        "beurten": [
            {
                "nr": 1,
                "ronde": 1,
                "spreker": "koning",
                "soort": "routering",
                "aan": "engineer",
                "vraag": "Check mill",
                "reden": "Start",
            },
            {
                "nr": 2,
                "ronde": 1,
                "spreker": "engineer",
                "soort": "bericht",
                "tekst": "ENG-901 first message.",
            },
            {
                "nr": 3,
                "ronde": 1,
                "spreker": "engineer",
                "soort": "bericht",
                "tekst": "ENG-902 second message without routering.",
            },
        ]
    }
    double_errors = validate.spelregels(trace_double_bericht)
    assert len(double_errors) == 1
    assert "beurt 3" in double_errors[0]

    # (c) validate.RARE_STREEP catches every Unicode dash variant and ignores ASCII '-'
    for dash in UNICODE_DASHES:
        assert validate.RARE_STREEP.search(f"Check ENG{dash}901 and ORA{dash}906.")
    assert not validate.RARE_STREEP.search("Check ENG-901 and ORA-906.")
