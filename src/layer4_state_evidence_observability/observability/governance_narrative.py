from typing import Any

from .models import Layer2RuntimeTrace, TraceGovernancePass

# `governance_exchange.md` is the raw DEBUG stream of every OPA request and response: it
# is the authoritative audit surface and must stay complete, which is exactly what makes
# it unreadable (megabytes of nested JSON per run). `trace_summary.md` is structured but
# node-by-node, so the exchange between actor and governance is spread across tables.
#
# This renderer answers a different question - "what did the agent and the governance
# actually say to each other, turn by turn?" - and is built from the exported trace, not
# from the live log stream, so it can also be produced for already-archived runs.

_DIRECTIVE_LABELS: dict[str, str] = {
    "continue": "PROSEGUI",
    "continue_read_only": "PROSEGUI (sola lettura)",
    "request_more_evidence": "TORNA INDIETRO — servono più prove",
    "revise_action": "TORNA INDIETRO — correggi la proposta",
    "replan": "RIPIANIFICA",
    "ask_user_clarification": "CHIEDI UN CHIARIMENTO",
    "wait_human_review": "FERMATI — serve una decisione umana",
    "stop": "BLOCCATO",
}

# Directives that are worth reading in full; the rest are routine forward progress.
_NOTABLE_DIRECTIVES = frozenset(
    {
        "request_more_evidence",
        "revise_action",
        "replan",
        "ask_user_clarification",
        "wait_human_review",
        "stop",
    }
)

_ACTOR_RESULT_LABELS: dict[str, str] = {
    "propose_action": "ha proposto un'azione",
    "intermediate_output": "ha prodotto un risultato intermedio",
    "step_completed": "ha dichiarato il passo completato",
    "ask_clarification": "ha chiesto un chiarimento",
    "blocked": "si è dichiarato bloccato",
    "needs_replan": "ha chiesto di ripianificare",
}

_DECISION_LABELS: dict[str, str] = {
    "approve_continue": "Approva",
    "approve_continue_read_only": "Approva in sola lettura",
    "modify_and_continue": "Modifica e prosegui",
    "request_more_evidence": "Richiedi più prove",
    "replan": "Ripianifica",
    "ask_clarification": "Chiedi un chiarimento",
    "abort": "Interrompi",
}

_REASON_LABELS: dict[str, str] = {
    "allowed_by_policy": "tutti i controlli attivi hanno dato l'assenso",
    "read_only_allowed": "l'inviluppo di sola lettura è rispettato",
    "evidence_anchoring_requires_review": "valori nel testo non riscontrati nelle fonti",
    "evidence_request_limit_reached": "prove insufficienti dopo ripetuti tentativi",
    "invalid_or_missing_selected_evidence": "prove citate non valide",
    "unanchored_locators_in_selection": "link citato mai recuperato",
    "unanchored_locators_in_universe": "link citato mai recuperato",
    "unanchored_literals_in_selection": "valore non riscontrato nelle fonti",
    "unanchored_literals_in_universe": "valore non riscontrato nelle fonti",
    "insufficient_independent_sources": "fonti indipendenti insufficienti",
    "retrieved_content_missing": "contenuto non davvero recuperato",
    "title_only_evidence_fragility": "selezione fatta di soli titoli",
    "circular_evidence_reference": "autocitazione",
    "replan_ceiling_reached": "troppe ripianificazioni",
    "consecutive_tool_failures": "troppi errori consecutivi degli strumenti",
    "step_exploration_budget_exhausted": "passo bloccato troppo a lungo",
    "action_churn": "l'azione proposta oscilla senza convergere",
    "unchanged_candidate_resubmission": "ripresentata la stessa identica proposta",
    "rejected_candidate_resubmitted": "ripresentata una proposta già respinta",
    "capability_approval_required": "serve un'autorizzazione umana esplicita",
    "judge_unsafe": "la verifica semantica ha giudicato l'azione non sicura",
    "judge_uncertain": "la verifica semantica non ha potuto garantire la sicurezza",
    "judge_needs_revision": "la verifica semantica chiede una correzione",
}


def _label(mapping: dict[str, str], key: str | None) -> str:
    if not key:
        return "—"
    return mapping.get(key, key)


def _reason_text(codes: tuple[str, ...]) -> str:
    if not codes:
        return "—"
    phrases = dict.fromkeys(_label(_REASON_LABELS, code) for code in codes)
    return "; ".join(phrases)


def _shorten(text: str | None, limit: int) -> str:
    if not text:
        return ""
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else f"{collapsed[:limit]}…"


# Surface the exact values a reviewer had to rule on, not just that a rule fired.
def _flagged_values(governance_pass: TraceGovernancePass) -> tuple[str, ...]:
    evidence = governance_pass.pillar_results.get("evidence")
    if not isinstance(evidence, dict):
        return ()
    facts = evidence.get("raw_facts")
    if not isinstance(facts, dict):
        return ()
    values: list[str] = []
    for key in (
        "unanchored_literals_in_selection",
        "unanchored_locators_in_selection",
    ):
        entries = facts.get(key)
        if isinstance(entries, list):
            values.extend(str(entry) for entry in entries)
    return tuple(dict.fromkeys(values))


def _build_header(trace: Layer2RuntimeTrace) -> list[str]:
    runtime = trace.runtime
    execution = trace.external_execution
    outcome = _label(
        {
            "completed": "completata",
            "aborted": "interrotta",
            "waiting_human_review": "in attesa di una decisione umana",
        },
        runtime.current_state,
    )
    decisions = len(trace.human_review.decision_history)
    lines = [
        "# Cosa si sono detti agente e governance",
        "",
        f"**Thread:** `{runtime.thread_id}`  ",
        f"**Obiettivo:** {_shorten(trace.intake.goal, 400)}  ",
        f"**Esito:** {outcome}",
        "",
        f"**Turni dell'agente:** {runtime.actor_turn_count} · "
        f"**Chiamate a strumenti:** {runtime.tool_call_count} · "
        f"**Decisioni umane:** {decisions} · "
        f"**Valutazioni di governance:** {len(trace.governance_passes)}",
    ]
    if execution is not None and getattr(execution, "status", None):
        lines.append("")
        lines.append(
            f"**Operazione esterna:** {execution.status}"
            + (
                f" · riferimento `{execution.external_reference}`"
                if getattr(execution, "external_reference", None)
                else ""
            )
        )
    return lines


# Condense the routine stretch between two notable moments into a single line, so the
# reader sees that work happened without reading one block per successful tool call.
def _flush_routine(count: int, lines: list[str]) -> None:
    if count <= 0:
        return
    lines.append("")
    lines.append(
        f"_…{count} valutazion{'e' if count == 1 else 'i'} di routine: la governance ha "
        "lasciato proseguire._"
    )


def _scratchpad_entries(trace: Layer2RuntimeTrace) -> list[dict[str, Any]]:
    return [entry for entry in trace.execution.scratchpad if isinstance(entry, dict)]


# Quote the actor's own recorded rationale for a step, never a reconstruction of it.
def _actor_rationale(entries: list[dict[str, Any]], step_id: str | None) -> str:
    if not step_id:
        return ""
    for entry in reversed(entries):
        if (
            entry.get("plan_step_id") == step_id
            and entry.get("entry_type") == "completion_rationale"
        ):
            return _shorten(entry.get("summary"), 400)
    return ""


def _build_timeline(trace: Layer2RuntimeTrace) -> list[str]:
    lines: list[str] = ["", "---", "", "## Cronologia"]
    scratchpad = _scratchpad_entries(trace)
    decisions = list(trace.human_review.decision_history)
    decision_index = 0
    routine_run = 0
    current_step: str | None = None
    # A remediation loop resubmits the same completion, so the actor's rationale repeats
    # verbatim. Quoting it once and marking the repeats keeps the loop visible - which is
    # the point of reading this file - without three identical paragraphs.
    last_rationale = ""

    for governance_pass in trace.governance_passes:
        notable = governance_pass.directive in _NOTABLE_DIRECTIVES
        if not notable:
            routine_run += 1
            continue

        _flush_routine(routine_run, lines)
        routine_run = 0

        step_id = governance_pass.evaluated_plan_step_id
        if step_id and step_id != current_step:
            current_step = step_id
            lines.append("")
            lines.append(f"### Passo `{step_id}`")

        lines.append("")
        lines.append(
            f"**L'agente** {_label(_ACTOR_RESULT_LABELS, governance_pass.evaluated_actor_result_type)}"
            + (
                f" usando `{governance_pass.evaluated_tool_name}`"
                if governance_pass.evaluated_tool_name
                else ""
            )
            + "."
        )
        rationale = _actor_rationale(scratchpad, step_id)
        if rationale and rationale != last_rationale:
            lines.append("")
            lines.append(f"> {rationale}")
            last_rationale = rationale
        elif rationale:
            lines.append("")
            lines.append("> _(stessa motivazione del tentativo precedente)_")
        lines.append("")
        lines.append(f"**La governance** ha risposto: **{_label(_DIRECTIVE_LABELS, governance_pass.directive)}**")
        lines.append("")
        lines.append(f"- Perché: {_reason_text(governance_pass.reason_codes)}")
        flagged = _flagged_values(governance_pass)
        if flagged:
            lines.append(f"- Valori segnalati: {', '.join(f'`{value}`' for value in flagged)}")

        if governance_pass.requires_human_review and decision_index < len(decisions):
            decision = decisions[decision_index]
            decision_index += 1
            lines.append("")
            lines.append(
                f"**L'operatore** ha deciso: **{_label(_DECISION_LABELS, decision.decision_type)}**"
                + (f" — {_shorten(decision.note, 200)}" if decision.note else "")
            )

    _flush_routine(routine_run, lines)
    return lines


# Close with the one thing a reader of this file usually came for.
def _build_outcome(trace: Layer2RuntimeTrace) -> list[str]:
    communication = trace.external_communication
    draft = getattr(communication, "draft", None) if communication is not None else None
    if draft is None:
        return []
    return [
        "",
        "---",
        "",
        "## Testo uscito dal sistema",
        "",
        f"**Destinatario:** {draft.recipient or '—'}  ",
        f"**Oggetto:** {draft.subject or '—'}",
        "",
        "```text",
        _shorten(draft.body, 4000) or "(vuoto)",
        "```",
    ]


# Build a turn-by-turn, human-readable account of one governed run.
def build_governance_narrative_markdown(trace: Layer2RuntimeTrace) -> str:
    lines: list[str] = []
    lines.extend(_build_header(trace))
    lines.extend(_build_timeline(trace))
    lines.extend(_build_outcome(trace))
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["build_governance_narrative_markdown"]
