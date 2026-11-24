"""Comprehensive King isolation, zero-leakage, and live trace+verdict submission audit tests."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from jsonschema import Draft202012Validator

import lek
import validate
from verify_gate import extract_ids

ROOT = Path(__file__).resolve().parent.parent
ROLE_SET = {"archmage", "beast_master", "engineer", "guardian_keeper", "historian", "oracle"}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_king_cannot_access_knowledge_sources_or_mcp() -> None:
    """AST & source audit: king.py never imports MCP, never reads kennisbronnen/, and has no file-read calls."""
    king_src = (ROOT / "king.py").read_text(encoding="utf-8")
    tree = ast.parse(king_src)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != "mcp", f"king.py must not import {alias.name}"
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] != "mcp", f"king.py must not import from {node.module}"
            for alias in node.names:
                assert "MCP" not in alias.name, f"king.py must not import MCP tool {alias.name}"
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                raise AssertionError("king.py must not open files directly")
            if isinstance(node.func, ast.Attribute) and node.func.attr in {"read_text", "read_bytes"}:
                raise AssertionError(f"king.py must not call .{node.func.attr}()")

    lowered = king_src.lower()
    assert "kennisbron" not in lowered
    assert "mcp_" not in lowered


def test_lek_detector_catches_synthetic_leaks_and_passes_real_repo() -> None:
    """Verify lek.py catches both leaked IDs and copied 5-word phrases, and passes on our repo."""
    fake_kb = "ENG-901: The bronze waterwheel axle slipped three notches during frost."
    leaked_id = lek.zoek(fake_kb, [("prompt.py", "Ask about ENG-901 right away.")])
    assert len(leaked_id) == 1 and "ENG-901" in leaked_id[0][1]

    leaked_phrase = lek.zoek(fake_kb, [("prompt.py", "Check if the bronze waterwheel axle slipped overnight.")])
    assert len(leaked_phrase) == 1 and "matching phrase" in leaked_phrase[0][1]

    clean_prompt = lek.zoek(fake_kb, [("prompt.py", "Ask each advisor for dated observations and rules.")])
    assert clean_prompt == []

    # Real repo check whenever local kennisbronnen/<rol>/* is present
    kb_files = sorted(p for p in (ROOT / "kennisbronnen").glob("*/*") if p.suffix in (".md", ".csv"))
    if kb_files:
        bron = "\n".join(p.read_text(encoding="utf-8") for p in kb_files)
        publiek = (ROOT / "rolkaarten.md").read_text(encoding="utf-8")
        findings = lek.zoek(bron, lek.bestanden(), publiek)
        assert findings == [], f"Unexpected KB leak(s) found by lek.py: {findings}"


def test_live_trace_and_verdict_full_submission_audit() -> None:
    """Detailed end-to-end audit of root trace.json and verdict.json."""
    trace_path = ROOT / "trace.json"
    verdict_path = ROOT / "verdict.json"
    assert trace_path.exists() and verdict_path.exists()

    trace = _load_json(trace_path)
    verdict = _load_json(verdict_path)

    # 1. Exact run_id match + strict JSON Schema + validate.spelregels
    assert trace["run_id"] == verdict["run_id"]
    trace_schema = _load_json(ROOT / "schema" / "trace.json")
    verdict_schema = _load_json(ROOT / "schema" / "verdict.json")
    assert list(Draft202012Validator(trace_schema).iter_errors(trace)) == []
    assert list(Draft202012Validator(verdict_schema).iter_errors(verdict)) == []
    assert validate.spelregels(trace) == []

    # 2. Zero malformed Unicode hyphens anywhere in trace or verdict
    assert not validate.RARE_STREEP.search(json.dumps(trace, ensure_ascii=False))
    assert not validate.RARE_STREEP.search(json.dumps(verdict, ensure_ascii=False))

    # 3. King isolation + strict per-role MCP isolation + routing before every member turn
    beurten = trace["beurten"]
    member_turns = 0
    seen_routing_for_next = False
    spoken_ids: set[str] = set()
    for b in beurten:
        spreker, soort = b["spreker"], b["soort"]
        if spreker == "koning":
            assert soort not in {"toolcall", "bestand"}, f"King accessed a source at step {b['nr']}"
            if soort == "routering":
                seen_routing_for_next = b.get("aan") is not None
        elif spreker in ROLE_SET:
            if soort == "toolcall":
                assert f"/{spreker}/mcp" in b["pad"], f"Role {spreker} called foreign MCP {b['pad']}"
            elif soort == "bericht":
                assert seen_routing_for_next, f"Member {spreker} spoke without preceding routing at step {b['nr']}"
                member_turns += 1
                spoken_ids.update(extract_ids(b["tekst"]))
                seen_routing_for_next = False

    assert 3 <= len(trace["raadsleden"]) <= 6
    assert 1 <= member_turns <= 10

    # 4. Grounding: EVERY ID cited in verdict.json and trace.geschillen was actually spoken by a member
    verdict_ids = set(extract_ids(json.dumps(verdict, ensure_ascii=False)))
    geschil_ids = set(extract_ids(json.dumps(trace["geschillen"], ensure_ascii=False)))
    assert verdict_ids <= spoken_ids, f"Unspoken IDs in verdict.json: {sorted(verdict_ids - spoken_ids)}"
    assert geschil_ids <= spoken_ids, f"Unspoken IDs in geschillen: {sorted(geschil_ids - spoken_ids)}"

    # 5. All 4 Extra Challenges present & valid
    routings = [b for b in beurten if b["soort"] == "routering"]
    assert any(r.get("aan") is None for r in routings) and member_turns < 10, "Expected Autonomous Early Stop"
    assert any(isinstance(r.get("open_vragen"), list) and len(r["open_vragen"]) > 0 for r in routings)
    assert routings[-1].get("open_vragen") == []
    for slot_name in ("ingreep", "mechanisme", "verloop"):
        slot = verdict["oorzaak"][slot_name]
        assert slot["bewering"] and slot["bewijs"]
        assert 0.0 <= float(slot["zekerheid"]) <= 1.0
    assert isinstance(verdict.get("voorspelling"), dict)
    assert isinstance(verdict["voorspelling"]["dag"], int) and verdict["voorspelling"]["dag"] >= 1

    # 6. Timing telemetry present on root submission run
    assert trace.get("gestart_op") and trace.get("voltooid_op")
    assert isinstance(trace.get("duur_s"), (int, float)) and trace["duur_s"] > 0
    assert all(b.get("tijdstip") for b in beurten)
