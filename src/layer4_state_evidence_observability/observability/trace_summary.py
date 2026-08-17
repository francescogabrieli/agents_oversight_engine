
from .models import (
    Layer2RuntimeTrace,
    TraceGovernancePass,
    TraceToolCall,
)


# Build a human-readable Markdown summary of one Layer 2 runtime trace.
def build_trace_summary_markdown(trace: Layer2RuntimeTrace) -> str:
    sections: list[str] = []
    sections.append(_build_header(trace))
    sections.append(_build_runtime_section(trace))
    sections.append(_build_intake_section(trace))
    sections.append(_build_plan_section(trace))
    sections.append(_build_actor_section(trace))
    sections.append(_build_tool_calls_section(trace))
    sections.append(_build_observations_section(trace))
    sections.append(_build_governance_section(trace))
    sections.append(_build_execution_section(trace))
    sections.append(_build_human_review_section(trace))
    sections.append(_build_external_communication_section(trace))
    sections.append(_build_external_execution_section(trace))
    return "\n\n".join(s for s in sections if s)


# Format the document title with trace version and thread identity.
def _build_header(trace: Layer2RuntimeTrace) -> str:
    return f"# Layer 2 Runtime Summary\n\n**Trace version:** `{trace.trace_version}`  \n**Thread ID:** `{trace.runtime.thread_id}`"


# Summarize the runtime identity, terminal state, and counters.
def _build_runtime_section(trace: Layer2RuntimeTrace) -> str:
    r = trace.runtime
    lines = [
        "## Runtime",
        "",
        "| Property | Value |",
        "|----------|-------|",
        f"| Current state | `{r.current_state}` |",
        f"| Bootstrap completed | {r.bootstrap_completed} |",
        f"| Actor turns | {r.actor_turn_count} |",
        f"| Tool calls | {r.tool_call_count} |",
        f"| Runtime flags | {', '.join(f'`{f}`' for f in r.runtime_flags) if r.runtime_flags else 'none'} |",
    ]
    if r.step_history:
        lines.append("")
        lines.append("### Step History")
        lines.append("")
        lines.append("| # | Node | State | Summary |")
        lines.append("|---|------|-------|---------|")
        for entry in r.step_history:
            lines.append(
                f"| {entry.get('step_index', '')} | `{entry.get('node_name', '')}` | `{entry.get('runtime_state', '')}` | {_truncate(str(entry.get('summary', '')), 80)} |"
            )
    return "\n".join(lines)


# Summarize the Layer 1 intake payload that seeded this run.
def _build_intake_section(trace: Layer2RuntimeTrace) -> str:
    i = trace.intake
    lines = [
        "## Task Intake",
        "",
        "| Property | Value |",
        "|----------|-------|",
        f"| Goal | {_truncate(i.goal, 200)} |",
        f"| Context | {_truncate(i.context, 200) if i.context else '—'} |",
        f"| Sensitivity | `{i.sensitivity}` |",
        f"| Parse status | `{i.parse_status}` |",
        f"| Read-only mode | {i.read_only_mode} |",
        f"| Execution intent | `{i.execution_intent}` |",
        f"| Confidence threshold | {i.confidence_threshold} |",
        f"| Max steps | {i.max_steps if i.max_steps is not None else 'unbounded'} |",
        f"| Max tool calls | {i.max_tool_calls if i.max_tool_calls is not None else 'unbounded'} |",
        f"| Policy profile | `{i.policy_profile_name}` |",
    ]
    if i.constraints:
        lines.append("")
        lines.append("**Constraints:**")
        for c in i.constraints:
            lines.append(f"- {c}")
    if i.success_criteria:
        lines.append("")
        lines.append("**Success criteria:**")
        for s in i.success_criteria:
            lines.append(f"- {s}")
    return "\n".join(lines)


# Render the semantic plan as a table with step statuses.
def _build_plan_section(trace: Layer2RuntimeTrace) -> str:
    lines = [
        "## Plan",
        "",
    ]
    if trace.planner_name:
        lines.append(f"**Planner:** `{trace.planner_name}`  ")
    if trace.planning_source:
        lines.append(f"**Planning source:** `{trace.planning_source}`  ")
    if trace.planner_rationale:
        lines.append(f"**Rationale:** {_truncate(trace.planner_rationale, 300)}")
    lines.append("")
    if not trace.plan_steps:
        lines.append("_No plan steps produced._")
        return "\n".join(lines)
    lines.append("| # | Step ID | Objective | Expected Outcome | Status |")
    lines.append("|---|---------|-----------|------------------|--------|")
    for idx, step in enumerate(trace.plan_steps, start=1):
        current_marker = " ←" if (
            trace.current_plan_step_id is not None and step.step_id == trace.current_plan_step_id
        ) else ""
        lines.append(
            f"| {idx} | `{step.step_id}` | {_truncate(step.objective, 80)} | {_truncate(step.expected_outcome, 60)} | `{step.status}`{current_marker} |"
        )
    return "\n".join(lines)


# Summarize the last actor turn including result type, confidence, and evidence refs.
def _build_actor_section(trace: Layer2RuntimeTrace) -> str:
    a = trace.actor
    if a.result_type is None:
        return ""
    lines = [
        "## Actor (Last Turn)",
        "",
        "| Property | Value |",
        "|----------|-------|",
        f"| Result type | `{a.result_type}` |",
        f"| Plan step | `{a.plan_step_id or '—'}` |",
        f"| Capability mode | `{a.capability_mode or '—'}` |",
        f"| External impact | {a.external_impact} |",
    ]
    if a.actor_self_confidence is not None:
        lines.append(f"| Self-confidence | {a.actor_self_confidence} |")
    if a.proposed_tool_name:
        lines.append(f"| Proposed tool | `{a.proposed_tool_name}` |")
    if a.blocking_reason:
        lines.append(f"| Blocking reason | {_truncate(a.blocking_reason, 120)} |")
    if a.summary:
        lines.append("")
        lines.append(f"**Summary:** {_truncate(a.summary, 400)}")
    if a.output_text:
        lines.append("")
        lines.append(f"**Output:** {_truncate(a.output_text, 400)}")
    if a.selected_evidence_refs:
        lines.append("")
        lines.append(f"**Selected evidence:** {', '.join(f'`{ref}`' for ref in a.selected_evidence_refs)}")
    return "\n".join(lines)


# Render all tool calls as a table with request details and outcome.
def _build_tool_calls_section(trace: Layer2RuntimeTrace) -> str:
    if not trace.tool_calls:
        return ""
    lines = [
        "## Tool Calls",
        "",
        "| # | Tool | Step | Reason | Success | Summary |",
        "|---|------|------|--------|---------|---------|",
    ]
    for idx, call in enumerate(trace.tool_calls, start=1):
        success_str = str(call.success) if call.success is not None else "pending"
        lines.append(
            f"| {idx} | `{call.tool_name}` | `{call.plan_step_id}` | {_truncate(call.reason, 50)} | {success_str} | {_truncate(call.summary or '—', 60)} |"
        )
        _append_tool_request_details(lines, call)
        if call.error_message:
            lines.append(f"  - **Error:** `{call.error_code or 'unknown'}` — {_truncate(call.error_message, 120)}")
        if call.observations:
            lines.append(f"  - **Observations:** {len(call.observations)}")
    return "\n".join(lines)


# Expand tool call parameters for non-trivial requests.
def _append_tool_request_details(lines: list[str], call: TraceToolCall) -> None:
    params: list[str] = []
    if call.query:
        params.append(f"query=`{_truncate(call.query, 80)}`")
    if call.url:
        params.append(f"url=`{_truncate(call.url, 120)}`")
    if call.text:
        params.append(f"text=`{_truncate(call.text, 80)}`")
    if call.path:
        params.append(f"path=`{call.path}`")
    if call.expression:
        params.append(f"expression=`{call.expression}`")
    if call.extractive_goal:
        params.append(f"extractive_goal=`{_truncate(call.extractive_goal, 80)}`")
    if params:
        lines.append(f"  - **Params:** {' · '.join(params)}")


# Render observations with truncated content for readability.
def _build_observations_section(trace: Layer2RuntimeTrace) -> str:
    if not trace.observations:
        return ""
    lines = [
        "## Evidence (Observations)",
        "",
    ]
    for obs in trace.observations:
        locator_str = f" — `{obs.locator}`" if obs.locator else ""
        lines.append(f"### `{obs.observation_id}`{locator_str}")
        lines.append("")
        lines.append(f"```\n{_truncate(obs.content, 500)}\n```")
        lines.append("")
    return "\n".join(lines).rstrip()


# Render each governance pass with pillar requirements, directive, and reason codes.
def _build_governance_section(trace: Layer2RuntimeTrace) -> str:
    if not trace.governance_passes:
        return ""
    lines = [
        "## Governance Passes",
        "",
    ]
    for gp in trace.governance_passes:
        lines.append(f"### Pass {gp.pass_index}")
        lines.append("")
        lines.append("| Property | Value |")
        lines.append("|----------|-------|")
        lines.append(f"| Evaluated step | `{gp.evaluated_plan_step_id or '—'}` |")
        lines.append(f"| Actor result type | `{gp.evaluated_actor_result_type}` |")
        if gp.evaluated_tool_name:
            lines.append(f"| Evaluated tool | `{gp.evaluated_tool_name}` |")
        if gp.evaluated_operation_digest:
            lines.append(f"| Operation digest | `{_truncate(gp.evaluated_operation_digest, 24)}` |")
        lines.append(f"| **Directive** | **`{gp.directive}`** |")
        lines.append(f"| Policy decision | `{gp.policy_decision}` |")
        lines.append(f"| Requires human review | {gp.requires_human_review} |")
        lines.append(f"| Policy bundle version | `{gp.policy_bundle_version}` |")
        lines.append("")
        _append_pillar_requirements(lines, gp)
        _append_pillar_results_summary(lines, gp)
        _append_pillar_errors(lines, gp)
        _append_reason_codes(lines, gp)
        if gp.safety_invariants:
            lines.append("")
            lines.append("**Safety invariants:**")
            for inv in gp.safety_invariants:
                lines.append(f"- {inv}")
        if gp.judge_admission:
            lines.append("")
            lines.append(f"**Judge admission:** `{gp.judge_admission.get('admission_reason', '—')}`")
        lines.append("")
    return "\n".join(lines).rstrip()


# Render which pillars OPA required for this governance pass.
def _append_pillar_requirements(lines: list[str], gp: TraceGovernancePass) -> None:
    if not gp.pillar_requirements:
        return
    lines.append("")
    lines.append("**Pillar requirements:**")
    for name, required in gp.pillar_requirements.items():
        marker = "required" if required else "not required"
        lines.append(f"- `{name}`: {marker}")


# Render a compact summary of each pillar's output.
def _append_pillar_results_summary(lines: list[str], gp: TraceGovernancePass) -> None:
    if not gp.pillar_results:
        return
    lines.append("")
    lines.append("**Pillar results:**")
    for name, result in gp.pillar_results.items():
        if not isinstance(result, dict):
            lines.append(f"- `{name}`: {_truncate(str(result), 120)}")
            continue
        summary_parts: list[str] = []
        if "summary" in result:
            summary_parts.append(_truncate(str(result["summary"]), 120))
        if "level" in result:
            summary_parts.append(f"level=`{result['level']}`")
        if "score" in result and result["score"] is not None:
            summary_parts.append(f"score={result['score']}")
        if "mode" in result:
            summary_parts.append(f"mode=`{result['mode']}`")
        if "policy_decision" in result:
            summary_parts.append(f"decision=`{result['policy_decision']}`")
        if "directive" in result:
            summary_parts.append(f"directive=`{result['directive']}`")
        if "recommendation" in result:
            summary_parts.append(f"recommendation=`{result['recommendation']}`")
        if "verdict" in result and result["verdict"] is not None:
            summary_parts.append(f"verdict=`{result['verdict']}`")
        if "severity" in result:
            summary_parts.append(f"severity=`{result['severity']}`")
        if "hard_deny" in result:
            summary_parts.append(f"hard_deny={result['hard_deny']}")
        if "invoked" in result:
            summary_parts.append(f"invoked={result['invoked']}")
        detail = " · ".join(summary_parts) if summary_parts else "—"
        lines.append(f"- `{name}`: {detail}")


# Surface any pillar-level errors for this governance pass.
def _append_pillar_errors(lines: list[str], gp: TraceGovernancePass) -> None:
    if not gp.pillar_errors:
        return
    lines.append("")
    lines.append("**Pillar errors:**")
    for name, error in gp.pillar_errors.items():
        lines.append(f"- `{name}`: {_truncate(error, 120)}")


# Render selected reason, rejected alternatives, and all reason codes.
def _append_reason_codes(lines: list[str], gp: TraceGovernancePass) -> None:
    if gp.selected_reason:
        lines.append("")
        lines.append(f"**Selected reason:** {_truncate(gp.selected_reason, 200)}")
    if gp.reason_codes:
        lines.append("")
        lines.append(f"**Reason codes:** {', '.join(f'`{c}`' for c in gp.reason_codes)}")
    if gp.rejected_alternatives:
        lines.append("")
        lines.append("**Rejected alternatives:**")
        for reason, explanation in gp.rejected_alternatives.items():
            lines.append(f"- `{reason}`: {_truncate(str(explanation), 120)}")


# Summarize executed steps, step validations, and scratchpad entries.
def _build_execution_section(trace: Layer2RuntimeTrace) -> str:
    ex = trace.execution
    has_content = (
        ex.final_output_text
        or ex.executed_steps
        or ex.step_validations
        or ex.scratchpad
    )
    if not has_content:
        return ""
    lines = ["## Execution Summary", ""]
    if ex.final_output_text:
        lines.append(f"**Final output:** {_truncate(ex.final_output_text, 500)}")
        lines.append("")
    if ex.executed_steps:
        lines.append("### Executed Steps")
        lines.append("")
        lines.append("| Step ID | Objective | Result Type | Summary |")
        lines.append("|---------|-----------|-------------|---------|")
        for step in ex.executed_steps:
            lines.append(
                f"| `{step.get('step_id', '')}` | {_truncate(str(step.get('objective', '')), 60)} | `{step.get('result_type', '')}` | {_truncate(str(step.get('summary', '')), 80)} |"
            )
        lines.append("")
    if ex.step_validations:
        lines.append("### Step Validations")
        lines.append("")
        for sv in ex.step_validations:
            refs = ", ".join(f"`{r}`" for r in sv.get("evidence_refs", []))
            lines.append(
                f"- **`{sv.get('plan_step_id', '')}`** — confidence={sv.get('actor_self_confidence', '—')}, "
                f"uncertainty={sv.get('residual_uncertainty', '—')}, refs=[{refs}]"
            )
            rationale = sv.get("rationale_summary")
            if rationale:
                lines.append(f"  - {_truncate(str(rationale), 200)}")
        lines.append("")
    if ex.scratchpad:
        lines.append("### Scratchpad")
        lines.append("")
        for entry in ex.scratchpad:
            refs = ", ".join(f"`{r}`" for r in entry.get("evidence_refs", []))
            lines.append(
                f"- Turn {entry.get('turn_index', '?')} (`{entry.get('plan_step_id', '')}`) "
                f"[{entry.get('entry_type', '')}]: {_truncate(str(entry.get('summary', '')), 120)}"
                + (f" — refs=[{refs}]" if refs else "")
            )
    return "\n".join(lines)


# Render pending and decided human review requests.
def _build_human_review_section(trace: Layer2RuntimeTrace) -> str:
    hr = trace.human_review
    has_content = hr.pending_request or hr.last_decision or hr.decision_history
    if not has_content:
        return ""
    lines = ["## Human Review", ""]
    if hr.pending_request:
        req = hr.pending_request
        lines.append("### Pending Request")
        lines.append("")
        lines.append(f"- **Request ID:** `{req.request_id}`")
        lines.append(f"- **State:** `{req.current_state}` → resume from `{req.resume_state}`")
        if req.current_plan_step_objective:
            lines.append(f"- **Plan step:** `{req.current_plan_step_id}` — {_truncate(req.current_plan_step_objective, 100)}")
        if req.governance_summary:
            lines.append(f"- **Governance:** {_truncate(req.governance_summary, 200)}")
        if req.review_summary:
            lines.append(f"- **Review summary:** {_truncate(req.review_summary, 200)}")
        if req.allowed_decisions:
            lines.append(f"- **Allowed decisions:** {', '.join(f'`{d}`' for d in req.allowed_decisions)}")
        if req.reason_codes:
            lines.append(f"- **Reason codes:** {', '.join(f'`{c}`' for c in req.reason_codes)}")
        if req.judge_findings:
            lines.append(f"- **Judge findings:** {'; '.join(req.judge_findings[:3])}")
        lines.append("")
    if hr.decision_history:
        lines.append("### Decision History")
        lines.append("")
        for dec in hr.decision_history:
            lines.append(f"- **`{dec.decision_type}`** (request `{dec.request_id}`)")
            if dec.note:
                lines.append(f"  - Note: {_truncate(dec.note, 120)}")
            if dec.clarification_text:
                lines.append(f"  - Clarification: {_truncate(dec.clarification_text, 120)}")
            if dec.external_communication_edit:
                lines.append("  - Draft edit applied: yes")
        lines.append("")
    elif hr.last_decision:
        dec = hr.last_decision
        lines.append(f"**Last decision:** `{dec.decision_type}` (request `{dec.request_id}`)")
    return "\n".join(lines)


# Render external communication phase and draft details.
def _build_external_communication_section(trace: Layer2RuntimeTrace) -> str:
    ec = trace.external_communication
    if ec is None:
        return ""
    lines = [
        "## External Communication",
        "",
        f"**Phase:** `{ec.phase}`",
    ]
    if ec.last_transition_summary:
        lines.append(f"**Last transition:** {_truncate(ec.last_transition_summary, 200)}")
    if ec.draft:
        d = ec.draft
        lines.append("")
        lines.append("### Draft")
        lines.append("")
        lines.append(f"- **Channel:** `{d.channel}`")
        if d.recipient:
            lines.append(f"- **Recipient:** {d.recipient}")
        if d.subject:
            lines.append(f"- **Subject:** {_truncate(d.subject, 120)}")
        if d.cc:
            lines.append(f"- **CC:** {', '.join(d.cc)}")
        if d.body:
            lines.append("")
            lines.append("**Body:**")
            lines.append("")
            lines.append(f"```\n{_truncate(d.body, 600)}\n```")
    if ec.edit_history:
        lines.append("")
        lines.append(f"**Edit history:** {len(ec.edit_history)} edit(s)")
    return "\n".join(lines)


# Render external execution intent, capability, and receipt history.
def _build_external_execution_section(trace: Layer2RuntimeTrace) -> str:
    ee = trace.external_execution
    if ee.capability_id is None and ee.execution_intent == "none":
        return ""
    lines = [
        "## External Execution",
        "",
        "| Property | Value |",
        "|----------|-------|",
        f"| Execution intent | `{ee.execution_intent}` |",
        f"| Capability ID | `{ee.capability_id or '—'}` |",
        f"| Payload digest | `{_truncate(ee.current_payload_digest or '—', 24)}` |",
        f"| Attempt count | {ee.attempt_count} |",
    ]
    if ee.last_receipt:
        r = ee.last_receipt
        lines.append("")
        lines.append("### Last Receipt")
        lines.append("")
        lines.append(f"- **Status:** `{r.status}`")
        lines.append(f"- **Operation ID:** `{r.operation_id}`")
        if r.external_reference:
            lines.append(f"- **External reference:** {r.external_reference}")
        if r.summary:
            lines.append(f"- **Summary:** {_truncate(r.summary, 200)}")
        if r.error_code:
            lines.append(f"- **Error code:** `{r.error_code}`")
    if ee.receipt_history and len(ee.receipt_history) > 1:
        lines.append("")
        lines.append("### Receipt History")
        lines.append("")
        for receipt in ee.receipt_history:
            lines.append(f"- Attempt {receipt.attempt_number}: `{receipt.status}` — `{receipt.operation_id}`")
    return "\n".join(lines)


# Truncate long strings for readability in the markdown output.
def _truncate(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


__all__ = ["build_trace_summary_markdown"]
