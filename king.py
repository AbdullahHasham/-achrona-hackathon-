"""Domain-general forensic King Orchestrator and Verdict Synthesizer.

The King has zero direct access to any knowledge source or external tool: all
routing decisions, cross-examinations, dispute resolutions, and final verdict
claims rely exclusively on statements and record IDs spoken by council members
during the debate.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Sequence, TypeVar

from agent_framework import Agent
from pydantic import BaseModel

from schemas_pydantic import (
    RouteringDecision,
    VerdictSynthesis,
    to_trace_geschillen,
    to_verdict_dict,
)
from verify_gate import (
    ROLE_TO_PREFIX,
    VALID_ID_RE,
    extract_ids,
    normalize_hyphens,
    verify_and_repair_verdict,
)

ModelT = TypeVar("ModelT", bound=BaseModel)
StructuredInvoker = Callable[
    [type[ModelT], str, str],
    Awaitable[tuple[ModelT, dict[str, Any] | None]],
]


def _merge_usage(
    first: dict[str, Any] | None,
    second: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Combine token usage dicts from an initial call and a fallback retry."""
    if not first:
        return dict(second) if isinstance(second, dict) else second
    if not second:
        return dict(first) if isinstance(first, dict) else first
    merged: dict[str, Any] = {}
    keys = (
        "input_token_count",
        "output_token_count",
        "total_token_count",
        "cache_read_input_token_count",
    )
    for key in set(first.keys()) | set(second.keys()):
        v1 = first.get(key)
        v2 = second.get(key)
        if key in keys or (isinstance(v1, (int, float)) and isinstance(v2, (int, float))):
            merged[key] = (v1 or 0) + (v2 or 0)
        else:
            merged[key] = v2 if v2 is not None else v1
    return merged


def _extract_json_object(text: str) -> str:
    """Extract the outermost JSON object from raw model text or fenced output."""
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Empty model response; expected a JSON object.")

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```"):
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model response.")

    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(cleaned)):
        ch = cleaned[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = cleaned[start : idx + 1]
                json.loads(candidate)
                return candidate

    raise ValueError("Unterminated JSON object in model response.")


def _validate_response_model(response: Any, model_cls: type[ModelT]) -> ModelT:
    """Read structured `.value` or fall back to extracting JSON from `.text`."""
    try:
        val = getattr(response, "value", None)
    except Exception:
        val = None

    if isinstance(val, model_cls):
        return val
    if isinstance(val, dict):
        return model_cls.model_validate(val)

    raw_text = getattr(response, "text", None)
    if isinstance(raw_text, str) and raw_text.strip():
        json_str = _extract_json_object(raw_text)
        return model_cls.model_validate_json(json_str)

    raise ValueError(f"Could not parse {model_cls.__name__} from response.")


def _as_agent(client_or_agent: Any, system_prompt: str) -> Any:
    """Wrap an LLM chat client in an Agent, or pass through an Agent-like object."""
    if hasattr(client_or_agent, "run") and callable(client_or_agent.run):
        return client_or_agent
    return Agent(client_or_agent, system_prompt, name="koning")


async def _parse_structured(
    client_or_agent: Any,
    model_cls: type[ModelT],
    system_prompt: str,
    user_prompt: str,
    options: dict[str, Any] | None = None,
) -> tuple[ModelT, dict[str, Any] | None]:
    """Run a structured LLM call with strict schema mode and json_object fallback."""
    agent = _as_agent(client_or_agent, system_prompt)
    base_options = dict(options) if options else {}
    strict_options = {**base_options, "response_format": model_cls}

    usage_first: dict[str, Any] | None = None
    first_error: Exception | None = None
    try:
        response = await agent.run(user_prompt, options=strict_options)
        usage_first = getattr(response, "usage_details", None)
        parsed = _validate_response_model(response, model_cls)
        return parsed, usage_first
    except Exception as exc:
        first_error = exc

    schema_json = json.dumps(model_cls.model_json_schema(), ensure_ascii=False)
    fallback_prompt = (
        f"{user_prompt}\n\n"
        "IMPORTANT: Return ONLY a single valid JSON object conforming strictly "
        f"to this JSON Schema (no extra keys, no markdown prose):\n{schema_json}"
    )
    json_options = {**base_options, "response_format": {"type": "json_object"}}
    try:
        retry_response = await agent.run(fallback_prompt, options=json_options)
        usage_second = getattr(retry_response, "usage_details", None)
        parsed = _validate_response_model(retry_response, model_cls)
        return parsed, _merge_usage(usage_first, usage_second)
    except Exception as retry_exc:
        raise RuntimeError(
            f"Failed to parse structured {model_cls.__name__} "
            f"(strict error: {first_error}; fallback error: {retry_exc})"
        ) from retry_exc


def _format_roster(
    leden: Sequence[Any],
    roles_spoken: dict[str, int] | None = None,
) -> str:
    """Format council member roster metadata for prompts."""
    lines: list[str] = []
    for lid in leden:
        rol = getattr(lid, "ROL", str(lid))
        naam = getattr(lid, "NAAM", rol)
        perspectief = getattr(lid, "PERSPECTIEF", "")
        belang = getattr(lid, "BELANG", None)
        parts = [f"- role=`{rol}` | name={naam}"]
        if roles_spoken is not None:
            parts.append(f"turns_spoken={roles_spoken.get(rol, 0)}")
        if perspectief:
            parts.append(f"perspective={perspectief}")
        if belang:
            parts.append(f"public_bias={belang}")
        lines.append(" | ".join(parts))
    return "\n".join(lines) if lines else "(no council members registered)"


def _collect_spoken_ids(
    beurten: Sequence[dict[str, Any]],
    debat: Sequence[str],
    valid_roles: set[str],
) -> list[str]:
    """Collect unique record IDs already spoken by council members."""
    seen: set[str] = set()
    ordered: list[str] = []
    sources: list[str] = [
        b.get("tekst", "")
        for b in beurten
        if b.get("soort") == "bericht" and b.get("spreker") in valid_roles
    ]
    if not sources:
        sources = list(debat)
    for text in sources:
        for cid in extract_ids(text):
            if cid not in seen:
                seen.add(cid)
                ordered.append(cid)
    return ordered


def _build_routing_system_prompt(language: str) -> str:
    return (
        "You are the King presiding over a multi-role forensic council.\n"
        "You have NO direct access to any knowledge source or external tool; "
        "you must rely EXCLUSIVELY on facts and record IDs spoken by council members.\n\n"
        "Core forensic orchestration rules:\n"
        "1. Bias & skepticism: Every council member has an institutional interest (Belang) and only sees "
        "their own department's records. Members may shift blame, omit inconvenient context, misread "
        "coincidences, or advocate drastic fixes. Never accept a single role's explanation or drastic "
        "remedy without cross-checking it against other roles' evidence.\n"
        "2. Source isolation awareness (CRITICAL): Every council member can ONLY read their own private "
        "knowledge source and can NEVER inspect foreign-role files or verify foreign record IDs. "
        "NEVER spend a turn asking Member A whether their file literally cites Member B's ID or whether "
        "two roles' records sit in the same file. Cross-role synthesis (connecting matching dates, times, "
        "wording, and rules across roles) is YOUR job as King — do not waste turns chasing impossible "
        "cross-file confirmations.\n"
        "3. Dynamic routing (NO fixed round-robin): Always pick the single highest-value next speaker "
        "to investigate the four required verdict dimensions from the debate itself:\n"
        "   - Pillar 1 — Trigger / Intervention (`ingreep`): What concrete action, change, or decision "
        "initiated or altered the situation, who was involved or authorized it, and on what exact dates and times.\n"
        "   - Pillar 2 — Causal Mechanism (`mechanisme`): What governing rules, charters, or technical "
        "specifications explain how the observed failures arise across domains. NEVER accept collapsed "
        "ID ranges when a member summarizes multiple rules or articles — require each relevant rule to be "
        "cited individually with its own record ID.\n"
        "   - Pillar 3 — Timeline & Recurrence (`verloop` & `voorspelling`): Pre-incident baseline, exact "
        "onset, full sequence of incident dates/times and exact per-date measurements (never accept pooled "
        "ranges when values change over time), whether any recurring pattern or observation-schedule gap "
        "exists, and whether the next occurrence day can be calculated from the verified dates.\n"
        "   - Pillar 4 — Safe Recovery & Risks (`acties` & `risicos`): What operational constraints, "
        "prerequisites, safety warnings, technical controls, or governance gaps govern remediation order "
        "and ongoing risks.\n"
        "4. Information-dense questions (`vraag`) & cross-examination: Pack 2–3 concrete, targeted "
        "sub-questions into each `vraag` requesting exact dates, times, numerical measurements, governing "
        "rules/charters, and exact record IDs. Whenever Member A's claim touches Member B's domain, makes "
        "an accusation, or contradicts Member B, route directly to Member B quoting Member A's specific "
        "claim and record IDs in `vraag`, and explain why in `reden`.\n"
        "5. Live `open_vragen` pruning & Early Stop discipline (`aan: null`): On EVERY turn, strictly PRUNE "
        "`open_vragen` by REMOVING (a) any question answered by the latest turn and (b) any question asking "
        "for logs or metrics that the relevant member explicitly confirmed do not exist in their source "
        "(record that as a known evidence limit in `werk_hypothese`, NEVER keep unanswerable zombie items "
        "in `open_vragen`!). You have a maximum budget of 10 member turns, and stopping cleanly BEFORE turn 10 "
        "once evidence is complete is explicitly rewarded. Once all active roles have spoken (at least 6–7 "
        "member turns) and all four verdict dimensions plus major contradictions are substantiated across "
        "roles with no remaining concrete answerable `open_vragen`, STOP EARLY (`aan: null`, `vraag: null`, "
        "`open_vragen: []`) with a clear `reden` explaining why evidence is complete. "
        "HOWEVER, NEVER stop early if any member collapsed multi-article rules or specifications into an ID "
        "range without citing the relevant articles individually, OR if an unresolved factual contradiction "
        "still needs a targeted cross-examination turn.\n"
        f"6. Language & ID formatting: Write `vraag`, `reden`, `open_vragen`, and `werk_hypothese` in {language}. "
        "Keep all JSON schema keys verbatim. Always write individual record IDs with a standard ASCII hyphen `-` "
        "(separate IDs with commas, NEVER slash-shorthand like `XXX-001/002`); never invent IDs."
    )


class KingOrchestrator:
    """Stateful forensic orchestrator that chooses each next council speaker."""

    def __init__(
        self,
        llm_client: Any = None,
        leden: Sequence[Any] | None = None,
        *,
        language: str = "English",
        options: dict[str, Any] | None = None,
        invoke_structured: StructuredInvoker | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.leden = list(leden) if leden is not None else []
        self.language = language
        self.options = options
        self.invoke_structured = invoke_structured
        self.open_vragen: list[str] = []
        self.werk_hypothese: str = ""

    def _valid_roles(self) -> list[str]:
        if self.leden:
            return [getattr(lid, "ROL") for lid in self.leden]
        return list(ROLE_TO_PREFIX.keys())

    async def _call_structured(
        self,
        model_cls: type[ModelT],
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[ModelT, dict[str, Any] | None]:
        if self.invoke_structured is not None:
            return await self.invoke_structured(model_cls, system_prompt, user_prompt)
        return await _parse_structured(
            self.llm_client,
            model_cls,
            system_prompt,
            user_prompt,
            options=self.options,
        )

    def _build_routing_user_prompt(
        self,
        *,
        gesproken: int,
        max_turns: int,
        roles_spoken: dict[str, int],
        roles_unspoken: list[str],
        spoken_ids: list[str],
        debat: Sequence[str],
    ) -> str:
        roster_block = _format_roster(self.leden, roles_spoken)
        open_q_block = (
            "\n".join(f"- {q}" for q in self.open_vragen)
            if self.open_vragen
            else "(none recorded yet)"
        )
        hypo_block = self.werk_hypothese or "(none yet)"
        ids_block = ", ".join(spoken_ids) if spoken_ids else "(none yet)"
        unspoken_block = ", ".join(roles_unspoken) if roles_unspoken else "(all roles have spoken)"
        transcript_block = "\n\n".join(debat) if debat else "(no council member has spoken yet)"

        early_stop_gate = ""
        if gesproken >= max(6, max_turns - 3) and not roles_unspoken:
            early_stop_gate = (
                f"\n\nEARLY-STOP READINESS CHECK (before turn {gesproken + 1} of {max_turns}): "
                "All council roles have already spoken. First PRUNE every answered or source-absent item "
                "from `open_vragen`. Do NOT route another turn just to ask one role to verify another role's "
                "private IDs or re-confirm non-existent logs. Check whether any member collapsed multi-article "
                "specifications/rules into an ID range or omitted a subsystem's protocol trigger rule — if so, "
                "spend ONE targeted follow-up turn unpacking those exact individual articles and IDs. "
                "Otherwise, if no concrete answerable Pillar 1–4 gap remains, STOP EARLY NOW with "
                "`aan: null`, `vraag: null`, and `open_vragen: []`."
            )

        return (
            f"Turn budget: {gesproken} of {max_turns} council member turns used "
            f"({max_turns - gesproken} remaining).\n\n"
            f"Council roster:\n{roster_block}\n\n"
            f"Unspoken roles: {unspoken_block}\n"
            f"Record IDs spoken so far: {ids_block}\n\n"
            f"Previous open questions (PRUNE answered & source-absent items!):\n{open_q_block}\n\n"
            f"Previous working hypothesis:\n{hypo_block}\n\n"
            f"Debate transcript so far:\n{transcript_block}\n\n"
            "Choose the single best next speaker (`aan`) and targeted `vraag`, OR set "
            "`aan: null` and `vraag: null` to stop if all four forensic pillars and "
            "major contradictions are thoroughly evidenced."
            f"{early_stop_gate}"
        )

    def _sanitize_decision(
        self,
        decision: RouteringDecision,
        valid_roles: list[str],
        roles_spoken: dict[str, int],
        roles_unspoken: list[str],
    ) -> RouteringDecision:
        valid_set = set(valid_roles)
        aan = decision.aan
        if aan is not None and aan not in valid_set:
            if roles_unspoken:
                aan = roles_unspoken[0]
            elif valid_roles:
                aan = min(valid_roles, key=lambda r: roles_spoken.get(r, 0))
            else:
                aan = None

        reden = normalize_hyphens((decision.reden or "").strip())
        werk_hypothese = normalize_hyphens((decision.werk_hypothese or "").strip())
        open_vragen = [
            cleaned
            for q in (decision.open_vragen or [])
            if isinstance(q, str) and (cleaned := normalize_hyphens(q.strip()))
        ]

        if aan is None:
            vraag: str | None = None
            open_vragen = []
            if not reden:
                reden = "Sufficient cross-role evidence gathered to conclude the debate."
        else:
            raw_vraag = normalize_hyphens((decision.vraag or "").strip())
            if not raw_vraag:
                raw_vraag = (
                    "Report all relevant facts from your knowledge source with exact dates, "
                    "times, numbers, rules, cross-role mentions, and exact record IDs."
                )
            vraag = raw_vraag
            if not reden:
                reden = f"Routing to {aan} to close remaining forensic gaps."

        return RouteringDecision(
            aan=aan,
            vraag=vraag,
            reden=reden,
            open_vragen=open_vragen,
            werk_hypothese=werk_hypothese,
        )

    async def decide_next(
        self,
        beurten: list[dict[str, Any]],
        debat: list[str],
        *,
        max_turns: int = 10,
    ) -> tuple[RouteringDecision, dict[str, Any] | None]:
        """Decide which council member speaks next, or stop the debate."""
        valid_roles = self._valid_roles()
        valid_set = set(valid_roles)
        roles_spoken = {r: 0 for r in valid_roles}
        gesproken = 0
        for b in beurten:
            if b.get("soort") == "bericht" and b.get("spreker") in valid_set:
                gesproken += 1
                roles_spoken[b["spreker"]] = roles_spoken.get(b["spreker"], 0) + 1

        if gesproken >= max_turns:
            self.open_vragen = []
            return (
                RouteringDecision(
                    aan=None,
                    vraag=None,
                    reden=f"Turn budget reached ({max_turns} council member turns).",
                    open_vragen=[],
                    werk_hypothese=self.werk_hypothese,
                ),
                None,
            )

        roles_unspoken = [r for r in valid_roles if roles_spoken.get(r, 0) == 0]
        spoken_ids = _collect_spoken_ids(beurten, debat, valid_set)

        system_prompt = _build_routing_system_prompt(self.language)
        user_prompt = self._build_routing_user_prompt(
            gesproken=gesproken,
            max_turns=max_turns,
            roles_spoken=roles_spoken,
            roles_unspoken=roles_unspoken,
            spoken_ids=spoken_ids,
            debat=debat,
        )

        decision, usage = await self._call_structured(
            RouteringDecision,
            system_prompt,
            user_prompt,
        )

        if decision.aan is not None and decision.aan not in valid_set:
            retry_prompt = (
                f"{user_prompt}\n\n"
                f"CORRECTION: Your previous choice `{decision.aan}` is not an active council role. "
                f"Choose `aan` strictly from {valid_roles} or `null`."
            )
            retry_decision, retry_usage = await self._call_structured(
                RouteringDecision,
                system_prompt,
                retry_prompt,
            )
            usage = _merge_usage(usage, retry_usage)
            decision = retry_decision

        sanitized = self._sanitize_decision(
            decision,
            valid_roles,
            roles_spoken,
            roles_unspoken,
        )
        self.open_vragen = list(sanitized.open_vragen)
        self.werk_hypothese = sanitized.werk_hypothese
        return sanitized, usage


def _build_synthesis_system_prompt(language: str) -> str:
    return (
        "You are the King synthesizing the final forensic Council Verdict and dispute log.\n"
        "You have NO direct access to any knowledge source; rely EXCLUSIVELY on what council "
        "members stated in the debate transcript and ONLY cite record IDs from the verified "
        "spoken allow-list provided.\n\n"
        "Synthesis requirements:\n"
        f"1. Write all natural-language text in {language}, while keeping ALL JSON field names "
        "strictly matching the schema keys (`koning_samenvatting_bericht`, `samenvatting`, "
        "`oorzaak`, `ingreep`, `mechanisme`, `verloop`, `bewering`, `bewijs`, `zekerheid`, "
        "`acties`, `actie`, `reden`, `risicos`, `risico`, `mitigatie`, `voorspelling`, `dag`, "
        "`geschillen`, `onderwerp`, `standpunten`, `uitkomst`).\n"
        "2. Cross-role synthesis (YOUR core job as King): Individual council members cannot see "
        "each other's private sources and will properly decline to verify foreign-role records. "
        "YOU see every verified statement across ALL roles. Actively synthesize matching dates, times, "
        "wording, parameter changes, and governing rules across roles to reach clear, cross-corroborated "
        "conclusions instead of leaving settled links unresolved.\n"
        "3. Strict citation integrity: Cite ONLY exact record IDs from the provided allow-list, "
        "using standard ASCII hyphen `-` (write each ID separately, never slash-shorthand). "
        "NEVER invent, alter, or guess an ID.\n"
        "4. `koning_samenvatting_bericht`: Crisp executive closing statement summarizing consensus, "
        "resolved disputes, root cause, recovery sequence, and forecast (if supported).\n"
        "5. `samenvatting`: Clear, decisive executive summary (never 'TODO').\n"
        "6. `oorzaak.{ingreep, mechanisme, verloop}` — MAKE EACH SLOT SELF-CONTAINED & THOROUGH "
        "(2–4 concrete sentences per slot, backed by multi-role `bewijs` IDs directly in that slot's "
        "`bewijs` array; do NOT leave key findings only in `samenvatting` or `voorspelling`!):\n"
        "   - `ingreep`: State what concrete intervention(s) or subsequent change(s) occurred, who "
        "carried out or authorized them based on cross-role evidence, and their exact dates and times.\n"
        "   - `mechanisme`: Explain the complete cross-domain causal chain in strict chronological order "
        "(earlier timestamps before later timestamps), linking the intervention(s), the governing "
        "rules/baselines, and the resulting cross-domain failures or anomalies reported in the transcript. "
        "Include every ID referenced in `mechanisme.bewering` inside `mechanisme.bewijs`.\n"
        "   - `verloop`: Document the complete temporal pattern in strict chronological order — "
        "pre-incident baseline, exact onset, full sequence of recurrence dates/times, exact per-date "
        "measurements (never cite a pre-change record as evidence of a post-change effect, and never "
        "contradict your own numbers), and any observation or sampling schedule gaps that explain why "
        "individual roles saw only subsets of the full pattern. Include every ID referenced in "
        "`verloop.bewering` inside `verloop.bewijs`.\n"
        "   Set honest `zekerheid` (0.0–1.0) reflecting cross-role corroboration. Only set `bewering: null` "
        "if genuinely unknown.\n"
        "7. `acties`: Strictly ordered remediation plan where list order IS execution order. Derive every "
        "prerequisite, safety warning, rejected unsafe option, technical step, interim safeguard, and "
        "governance action strictly from constraints and evidence stated in the transcript. Keep each "
        "step distinct and atomic, with a concrete `reden` and supporting `bewijs` (listing every ID "
        "cited in `actie` or `reden`).\n"
        "8. `risicos`: Concrete operational, security, data/integrity, and governance risks evidenced in "
        "the transcript, each paired with an actionable `mitigatie`.\n"
        "9. `voorspelling`: If the verified incident dates establish a clear recurrence period, FIRST "
        "identify the MAXIMUM (latest) incident day observed across ALL council members' records "
        "(do NOT stop at an earlier mid-series summary record!), verify that `voorspelling.dag` is "
        "STRICTLY GREATER than the latest observed day in the entire dataset, add the true event "
        "recurrence period (do NOT mistake a coarser sampling interval for the true event period), "
        "show the exact arithmetic in `bewering`, and include the supporting `bewijs` IDs (including "
        "the latest incident ID). Otherwise set `null`.\n"
        "10. `geschillen`: Record EVERY major contradiction, rival causal theory, attribution clash, "
        "or disputed claim raised across the debate. Every `geschil` MUST have valid council role keys "
        "in `standpunten` (never empty!), a decisive evidence-backed `uitkomst` whenever the combined "
        "cross-role records settle the clash (or `null` if genuinely unresolved), and decisive multi-role "
        "`bewijs` IDs. STRICT INTEGRITY: NEVER attribute a stance to a role on a topic that role never "
        "addressed in the transcript; distinguish 'role reported no record of X' from 'role denied X'; "
        "and do NOT leave a causal dispute as `uitkomst: null` when combined cross-role evidence already "
        "establishes the corroborated sequence while showing a rival claim is unsupported inference."
    )


def _verdict_completeness(verdict: dict[str, Any], geschillen: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Compare verdict completeness strictly from structural signals (zero domain facts)."""
    oorzaak = verdict.get("oorzaak") or {}
    filled_slots = sum(
        1 for k in ("ingreep", "mechanisme", "verloop")
        if isinstance(oorzaak.get(k), dict) and bool(oorzaak[k].get("bewering")) and bool(oorzaak[k].get("bewijs"))
    )
    has_forecast = 1 if isinstance(verdict.get("voorspelling"), dict) else 0
    evidence_breadth = sum(
        len(oorzaak[k].get("bewijs") or [])
        for k in ("ingreep", "mechanisme", "verloop")
        if isinstance(oorzaak.get(k), dict)
    ) + len(geschillen)
    return (filled_slots + has_forecast, len(verdict.get("acties") or []), evidence_breadth)


async def synthesize_verdict_and_geschillen(
    llm_client: Any,
    *,
    run_id: str,
    debat: list[str],
    beurten: list[dict[str, Any]],
    leden: Sequence[Any],
    spoken_member_ids: set[str],
    language: str = "English",
    options: dict[str, Any] | None = None,
    invoke_structured: StructuredInvoker | None = None,
) -> tuple[str, dict[str, Any], list[dict[str, Any]], list[str], dict[str, Any] | None]:
    """Synthesize, self-critique, verify, and schema-repair the final verdict and disputes."""
    allowed_ids = sorted(
        {
            norm
            for raw_id in spoken_member_ids
            if isinstance(raw_id, str)
            for norm in (normalize_hyphens(raw_id.strip()),)
            if VALID_ID_RE.match(norm)
        }
    )

    system_prompt = _build_synthesis_system_prompt(language)
    roster_block = _format_roster(leden)
    transcript_block = "\n\n".join(debat) if debat else "(no debate turns recorded)"
    ids_block = ", ".join(allowed_ids) if allowed_ids else "(none)"
    member_turns = sum(
        1
        for b in beurten
        if b.get("soort") == "bericht" and b.get("spreker") in ROLE_TO_PREFIX
    )

    user_prompt = (
        f"Run ID: {run_id}\n"
        f"Completed council member turns: {member_turns}\n\n"
        f"Council roster:\n{roster_block}\n\n"
        f"Verified spoken record IDs (ONLY cite IDs from this exact list):\n{ids_block}\n\n"
        f"Full debate transcript:\n{transcript_block}\n\n"
        "Produce the complete structured VerdictSynthesis now."
    )

    if invoke_structured is not None:
        synth, usage = await invoke_structured(VerdictSynthesis, system_prompt, user_prompt)
    else:
        synth, usage = await _parse_structured(
            llm_client,
            VerdictSynthesis,
            system_prompt,
            user_prompt,
            options=options,
        )

    raw_verdict = to_verdict_dict(run_id, synth)
    raw_geschillen = to_trace_geschillen(synth.geschillen)

    clean_verdict, clean_geschillen, repair_warnings = verify_and_repair_verdict(
        raw_verdict,
        raw_geschillen,
        set(allowed_ids),
    )

    # Pass 2: Critical self-review against the transcript (live runs only; safe fallback to Pass 1)
    if invoke_structured is None and llm_client is not None:
        warnings_block = (
            "Automated verifier notes to resolve:\n" + "\n".join(f"- {w}" for w in repair_warnings) + "\n\n"
            if repair_warnings
            else ""
        )
        critique_prompt = (
            f"{user_prompt}\n\n"
            f"YOUR FIRST DRAFT:\n{json.dumps({'verdict': clean_verdict, 'geschillen': clean_geschillen}, ensure_ascii=False)}\n\n"
            f"{warnings_block}"
            "CRITICAL SELF-REVIEW: Audit your draft critically against the full debate transcript above as if someone else wrote it:\n"
            "1. Does every date, time, name, and number match the transcript verbatim?\n"
            "2. Did any key corroborated finding (e.g. authorization/approval links, governing rules/articles, "
            "or cross-domain failure mechanisms mentioned in the transcript or in `acties`/`risicos`) get left out "
            "of `oorzaak.{ingreep, mechanisme, verloop}`? Also ensure `oorzaak.verloop.bewering` explicitly states "
            "the recurrence period/cadence itself (not only in `samenvatting` or `voorspelling`).\n"
            "3. Check `acties` for internal contradictions and ordering: strictly honor every prerequisite, "
            "execution order, and safety warning stated in the transcript; NEVER adopt a biased advisor's drastic "
            "remedy if it contradicts a documented safety warning — explicitly reject the unsafe option instead, "
            "and preserve the exact technical method/step size cited in the transcript.\n"
            "4. Ensure `risicos` covers the risk of any rejected drastic fix, any operational/capacity constraint "
            "on prerequisites, and the ongoing risk during the next predicted occurrence window.\n"
            "5. If a recurrence pattern exists, is `voorspelling.dag` strictly greater than the latest observed day across all roles, "
            "using the true event period?\n"
            "6. Are all major cross-role contradictions accurately captured in `geschillen` with valid role keys?\n"
            "Return the complete improved VerdictSynthesis JSON citing ONLY IDs from the verified allow-list."
        )
        try:
            synth2, usage2 = await _parse_structured(
                llm_client,
                VerdictSynthesis,
                system_prompt,
                critique_prompt,
                options=options,
            )
            usage = _merge_usage(usage, usage2)
            raw_v2 = to_verdict_dict(run_id, synth2)
            raw_g2 = to_trace_geschillen(synth2.geschillen)
            clean_v2, clean_g2, warnings2 = verify_and_repair_verdict(
                raw_v2,
                raw_g2,
                set(allowed_ids),
            )
            if _verdict_completeness(clean_v2, clean_g2) >= _verdict_completeness(clean_verdict, clean_geschillen):
                synth, clean_verdict, clean_geschillen, repair_warnings = synth2, clean_v2, clean_g2, warnings2
        except Exception:
            pass

    closing_text = normalize_hyphens((synth.koning_samenvatting_bericht or "").strip())
    if not closing_text:
        closing_text = normalize_hyphens(
            (clean_verdict.get("samenvatting") or "Council debate concluded.").strip()
        )

    # Guarantee required non-empty fields per schema/verdict.json
    samenvatting = (clean_verdict.get("samenvatting") or "").strip()
    if not samenvatting or samenvatting.upper() == "TODO":
        clean_verdict["samenvatting"] = closing_text
        repair_warnings.append("samenvatting: replaced empty/placeholder summary with closing summary")

    valid_acties = [
        a
        for a in (clean_verdict.get("acties") or [])
        if isinstance(a, dict)
        and isinstance(a.get("actie"), str)
        and a["actie"].strip()
        and isinstance(a.get("reden"), str)
        and a["reden"].strip()
    ]
    if not valid_acties:
        valid_acties = [
            {
                "actie": "Conduct a targeted follow-up investigation before making system changes.",
                "reden": "No verified remediation steps were returned by the synthesis model.",
                "bewijs": [],
            }
        ]
        repair_warnings.append("acties: inserted fallback action because acties list was empty")
    clean_verdict["acties"] = valid_acties

    valid_risicos = [
        r
        for r in (clean_verdict.get("risicos") or [])
        if isinstance(r, dict)
        and isinstance(r.get("risico"), str)
        and r["risico"].strip()
        and isinstance(r.get("mitigatie"), str)
        and r["mitigatie"].strip()
    ]
    if not valid_risicos:
        valid_risicos = [
            {
                "risico": "Incomplete forensic evidence could lead to an unsafe intervention.",
                "mitigatie": "Verify prerequisites and safety constraints across all council roles before acting.",
            }
        ]
        repair_warnings.append("risicos: inserted fallback risk because risicos list was empty")
    clean_verdict["risicos"] = valid_risicos

    clean_geschillen = [
        g
        for g in clean_geschillen
        if isinstance(g.get("onderwerp"), str) and g["onderwerp"].strip()
    ]

    return closing_text, clean_verdict, clean_geschillen, repair_warnings, usage
