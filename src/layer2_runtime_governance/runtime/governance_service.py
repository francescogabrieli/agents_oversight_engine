from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace

from layer1_intake import ExecutionIntent
from layer3_tool_bus_external_capabilities.external_operations import (
    ExternalExecutionAuthorizationSource,
    GovernanceActionFeedback,
    JudgeFindingSnapshot,
    OperationBoundary,
    build_external_execution_request,
)
from layer3_tool_bus_external_capabilities.tools import ToolGatewayConfig
from layer4_state_evidence_observability.behavioral import (
    BehavioralStorePort,
    persist_behavioral_runtime_snapshot,
    persist_judge_snapshot,
)

from ..actor_contract import ActorResultType
from ..domain import (
    ExternalCommunicationPhase,
    RuntimeState,
    TaskState,
    TerminationReason,
    set_external_communication_phase,
)
from ..domain.step_recovery import (
    resolve_evidence_episode_id,
    restore_actor_step_for_more_evidence,
)
from ..governance_components import (
    GovernanceDecision,
    GovernanceDirective,
    PolicyEnginePort,
    PolicyEvaluationInput,
    evaluate_opa_first_governance,
)
from ..hitl import build_human_review_request
from ..judge import JudgePort

# Nodes.py contains the executable Layer 2 business logic.
# At this stage the runtime performs bootstrap, planning, and the actor/tool loop.
from .actor_service import (
    commit_completion_candidate,
    commit_terminal_completion_candidate,
)
from .lifecycle_service import _append_step_record


_EVIDENCE_REFERENCE_FACTS = {
    "freshness_metadata_missing": "freshness_missing_observation_ids",
    "stale_evidence": "stale_evidence_observation_ids",
    "future_source_timestamp": "future_source_timestamp_observation_ids",
    "circular_evidence_reference": "circular_evidence_observation_ids",
}
_EVIDENCE_LITERAL_FACTS = {
    "unanchored_literals_in_selection": "unanchored_literals_in_selection",
    "unanchored_literals_in_universe": "unanchored_literals_in_universe",
}
_EVIDENCE_REMEDIATION_SUMMARIES = {
    "freshness_metadata_missing": (
        "Replace these observations or fetch their specific dated documents; "
        "an undated landing or index page cannot satisfy declared freshness."
    ),
    "stale_evidence": (
        "Remove or replace these observations because their source timestamps "
        "fall outside the declared freshness window."
    ),
    "future_source_timestamp": (
        "Remove or independently verify these observations because their source "
        "timestamps are later than the evaluation reference time."
    ),
    "unanchored_literals_in_selection": (
        "Remove or correct these literal claims, or select concrete observations "
        "that contain the same supported values."
    ),
    "unanchored_literals_in_universe": (
        "These literal claims are absent from every observation; retrieve direct "
        "support or remove them before completing the step."
    ),
    "insufficient_independent_sources": (
        "Retrieve and select enough observations from independent source origins."
    ),
    "retrieved_content_missing": (
        "Fetch the specific source documents instead of relying on search titles or locators."
    ),
}


# Advance both the whole-run audit counter and the bounded current episode.
def _record_evidence_request(state: TaskState, *, operation_id: str) -> None:
    state.evidence_request_count += 1
    if state.evidence_request_episode_id != operation_id:
        state.evidence_request_episode_id = operation_id
        state.evidence_request_episode_count = 0
    state.evidence_request_episode_count += 1


# Convert Evidence raw facts into concrete, actor-visible remediation findings.
def _build_evidence_feedback_findings(
    *,
    codes: tuple[str, ...],
    facts: dict[str, object],
    observations: tuple | list = (),
) -> tuple[JudgeFindingSnapshot, ...]:
    findings: list[JudgeFindingSnapshot] = []
    for code in codes:
        reference_field = _EVIDENCE_REFERENCE_FACTS.get(code)
        reference_values = facts.get(reference_field, []) if reference_field else []
        evidence_refs = (
            tuple(str(item) for item in reference_values)
            if isinstance(reference_values, list | tuple)
            else ()
        )
        literal_field = _EVIDENCE_LITERAL_FACTS.get(code)
        literal_values = facts.get(literal_field, []) if literal_field else []
        literals = (
            tuple(str(item) for item in literal_values)
            if isinstance(literal_values, list | tuple)
            else ()
        )
        summary = _EVIDENCE_REMEDIATION_SUMMARIES.get(
            code,
            f"Resolve the observed Evidence deficit '{code}' before resubmission.",
        )
        if code == "insufficient_independent_sources":
            current_count = facts.get('distinct_independent_sources_count', 0)
            required_count = facts.get('min_independent_sources_required', 0)
            summary = (
                f"{summary} Current distinct sources: "
                f"{current_count}; required: "
                f"{required_count}."
            )
            # Enrich with actionable guidance: how many search results remain unexplored.
            unexplored = _count_unexplored_search_results(observations)
            if unexplored > 0:
                gap = max(0, int(required_count) - int(current_count)) if required_count else 0
                summary += (
                    f" You have {unexplored} unfetched search results available."
                    f" Open at least {max(gap, 1)} more from different source origins"
                    f" before completing the step."
                )
            else:
                summary += (
                    " No unfetched search results remain; consider issuing a"
                    " new web_search with alternative queries to find additional sources."
                )
        findings.append(
            JudgeFindingSnapshot(
                category="evidence",
                code=code,
                severity="medium",
                summary=summary,
                evidence_refs=evidence_refs,
                payload_excerpt="; ".join(literals[:8]) if literals else None,
            )
        )
    return tuple(findings)


def _count_unexplored_search_results(
    observations: tuple | list,
) -> int:
    """Count search-result observations whose URLs have not been fetched yet.

    A search-result observation is identified by its content starting with
    ``Search result title:`` or lacking a locator with an http(s) scheme while
    still carrying a URL-shaped locator. A fetched observation has a locator
    that was also used as a ``fetch_url_text`` target (i.e. appears on a
    non-search observation). This is a lightweight heuristic consistent with
    the adapter contracts in Layer 3.
    """
    from urllib.parse import urlparse

    search_urls: set[str] = set()
    fetched_urls: set[str] = set()
    for obs in observations:
        locator = getattr(obs, "locator", None) or ""
        is_search_stub = obs.content.startswith("Search result title:")
        if is_search_stub:
            if locator:
                search_urls.add(locator)
            continue
        # Non-stub observations with an http(s) locator are fetched content.
        parsed = urlparse(locator)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            fetched_urls.add(locator)

    return len(search_urls - fetched_urls)


# Apply a validated policy outcome to canonical runtime state.
def governance(
    state: TaskState,
    *,
    policy_engine_service: PolicyEnginePort,
    judge_service: JudgePort | None = None,
    behavioral_store_service: BehavioralStorePort | None = None,
    tool_gateway_config: ToolGatewayConfig | None = None,
    thread_id: str | None = None,
    hitl_event_callback: Callable[[TaskState], None] | None = None,
) -> TaskState:
    if state.actor_result is None:
        _append_step_record(
            state,
            node_name="governance",
            summary="Governance skipped because there was no actor result to evaluate.",
        )
        return state

    resolved_thread_id = thread_id or state.runtime_thread_id or state.intake_metadata.request_id
    governance_evaluation = evaluate_opa_first_governance(
        task_state=state,
        policy_engine_service=policy_engine_service,
        judge_service=judge_service,
        behavioral_store_service=behavioral_store_service,
        tool_gateway_config=tool_gateway_config,
        thread_id=resolved_thread_id,
    )
    policy_input = governance_evaluation.policy_input
    static_policy_assessment = governance_evaluation.static_policy_assessment
    dynamic_policy_assessment = governance_evaluation.dynamic_policy_assessment
    behavioral_assessment = governance_evaluation.behavioral_assessment
    judge_assessment = governance_evaluation.judge_assessment
    governance_decision = governance_evaluation.governance_decision

    state.static_policy_history.append(static_policy_assessment)
    state.dynamic_policy_history.append(dynamic_policy_assessment)
    # Keep the last usable shadow sample visible instead of replacing it with
    # a deferred/unavailable marker from an intermediate governance turn.
    if not (
        behavioral_assessment.status == "unavailable"
        and state.behavioral_history
        and state.behavioral_history[-1].status != "unavailable"
    ):
        state.behavioral_history.append(behavioral_assessment)
    state.judge_history.append(judge_assessment)
    state.governance_history.append(governance_decision)
    _apply_governance_decision(
        state,
        governance_decision=governance_decision,
        policy_input=policy_input,
    )

    if state.current_state == RuntimeState.WAITING_HUMAN_REVIEW:
        state.pending_human_review_request = build_human_review_request(
            state=state,
            thread_id=resolved_thread_id,
        )
        if hitl_event_callback is not None:
            # Emit the HITL event before the graph pauses so the control plane can surface it.
            hitl_event_callback(deepcopy(state))
    else:
        state.pending_human_review_request = None
        state.human_review_resume_state = None

    # Materialize the cross-thread read model only at stable audit boundaries.
    if state.current_state in {
        RuntimeState.WAITING_HUMAN_REVIEW,
        RuntimeState.COMPLETED,
        RuntimeState.ABORTED,
    }:
        try:
            persist_behavioral_runtime_snapshot(
                thread_id=resolved_thread_id,
                intake_result=state.intake_payload,
                task_state=state,
                behavioral_store=behavioral_store_service,
            )
        except Exception as exc:  # noqa: BLE001
            _append_step_record(
                state,
                node_name="behavioral_persistence",
                summary=(
                    "Behavioral snapshot persistence failed without changing the "
                    f"runtime directive ({type(exc).__name__})."
                ),
            )
    else:
        try:
            persist_judge_snapshot(
                thread_id=resolved_thread_id,
                task_state=state,
                behavioral_store=behavioral_store_service,
            )
        except Exception as exc:  # noqa: BLE001
            _append_step_record(
                state,
                node_name="judge_persistence",
                summary=(
                    "Judge snapshot persistence failed without changing the "
                    f"runtime directive ({type(exc).__name__})."
                ),
            )

    _append_step_record(
        state,
        node_name="governance",
        summary=(
            "Governance evaluated the actor outcome and produced "
            f"directive '{governance_decision.directive.value}'."
        ),
    )
    return state

# Apply a validated policy outcome to canonical runtime state.
def _apply_governance_decision(
    state: TaskState,
    *,
    governance_decision: GovernanceDecision,
    policy_input: PolicyEvaluationInput,
) -> None:
    if governance_decision.directive == GovernanceDirective.STOP:
        set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.BLOCKED,
            summary="Governance denied the external communication branch.",
            review_ready=False,
        )
        state.termination_reason = TerminationReason.GOVERNANCE_DENIED
        state.current_state = RuntimeState.ABORTED
        state.current_tool_request = None
        state.pending_tool_requests = []
        state.pending_human_review_request = None
        state.human_review_resume_state = None
        return

    # A typed provider protocol failure is recoverable runtime state; it must
    # re-enter replanning before any generic review recommendation is applied.
    # An exhausted local recovery instead arrives as NEEDS_REPLAN and follows the
    # OPA directive, so repeated requests without a new observable basis reach HITL.
    if (
        state.actor_result is not None
        and state.actor_result.provider_protocol_failure
        and state.actor_result.result_type != ActorResultType.NEEDS_REPLAN
    ):
        state.pending_human_review_request = None
        state.human_review_resume_state = None
        state.current_state = RuntimeState.REPLANNING
        state.current_tool_request = None
        state.pending_tool_requests = []
        return

    if governance_decision.directive in {
        GovernanceDirective.WAIT_HUMAN_REVIEW,
        GovernanceDirective.ASK_USER_CLARIFICATION,
    }:
        review_ready = (
            state.external_communication.review_ready
            if state.external_communication is not None
            else False
        )
        set_external_communication_phase(
            state,
            phase=(
                ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
                if review_ready
                else (
                    ExternalCommunicationPhase.DRAFT_IN_PROGRESS
                    if state.external_communication is not None
                    and state.external_communication.draft is not None
                    else ExternalCommunicationPhase.EVIDENCE_COLLECTION
                )
            ),
            summary=(
                "The draft proposal reached a typed human review gate before any external execution."
                if review_ready
                else "Human input is required while the local draft or evidence collection remains in progress."
            ),
            review_ready=review_ready,
        )
        state.human_review_resume_state = _infer_human_review_resume_state(
            state=state,
            policy_input=policy_input,
        )
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        return

    if governance_decision.directive == GovernanceDirective.REPLAN:
        set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.EVIDENCE_COLLECTION,
            summary="Governance redirected the external communication branch back to replanning.",
            review_ready=False,
        )
        state.pending_human_review_request = None
        state.human_review_resume_state = None
        state.current_state = RuntimeState.REPLANNING
        state.current_tool_request = None
        return

    if governance_decision.directive == GovernanceDirective.REVISE_ACTION:
        _apply_action_revision(state, governance_decision=governance_decision)
        return

    if governance_decision.directive == GovernanceDirective.REQUEST_MORE_EVIDENCE:
        operation_id = resolve_evidence_episode_id(state)
        _record_evidence_request(state, operation_id=operation_id)
        set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.EVIDENCE_COLLECTION,
            summary="Governance required more evidence before the external communication draft can progress.",
            review_ready=False,
        )
        state.pending_human_review_request = None
        state.human_review_resume_state = None

        evidence_payload = (governance_decision.pillar_results or {}).get("evidence", {})
        evidence_facts = evidence_payload.get("raw_facts", {}) if isinstance(evidence_payload, dict) else {}
        deficit_codes = evidence_facts.get("deficit_codes", []) if isinstance(evidence_facts, dict) else []
        reason_codes = tuple(governance_decision.reason_codes or ["invalid_or_missing_selected_evidence"])
        all_codes = tuple(dict.fromkeys(str(c) for c in (deficit_codes or reason_codes)))
        state.governance_action_feedback.append(
            GovernanceActionFeedback(
                operation_id=operation_id,
                invalidated_payload_digest=(
                    state.external_operation_proposal.payload_digest
                    if state.external_operation_proposal is not None
                    else None
                ),
                finding_codes=all_codes,
                summary=governance_decision.summary,
                revision_index=state.evidence_request_episode_count,
                invalidated_evidence_selection_digest=(
                    state.pending_completion_candidate.evidence_selection_digest
                    if state.pending_completion_candidate is not None
                    else None
                ),
                findings=_build_evidence_feedback_findings(
                    codes=all_codes,
                    facts=evidence_facts,
                    observations=tuple(state.observations),
                ),
                judge_summary=(
                    "Governance required additional evidence "
                    f"(episode request {state.evidence_request_episode_count}; "
                    f"whole-run request {state.evidence_request_count})."
                ),
                judge_rationale=(
                    governance_decision.combiner_selected_reason
                    or governance_decision.summary
                ),
            )
        )

        # A governed read-only request is the recovery mechanism for an evidence gap.
        if (
            policy_input.actor_result_type == ActorResultType.PROPOSE_ACTION
            and state.current_tool_request is not None
        ):
            state.current_state = RuntimeState.READY_TOOL_EXECUTION
            return
        restore_actor_step_for_more_evidence(state)
        state.current_state = RuntimeState.ACTING
        state.current_tool_request = None
        return

    if _route_authorized_external_operation(
        state=state,
        governance_decision=governance_decision,
    ):
        return

    if policy_input.actor_result_type == ActorResultType.PROPOSE_ACTION:
        state.current_state = RuntimeState.READY_TOOL_EXECUTION
        return

    if policy_input.actor_result_type == ActorResultType.NEEDS_REPLAN:
        state.pending_human_review_request = None
        state.human_review_resume_state = None
        state.current_state = RuntimeState.REPLANNING
        return

    if policy_input.actor_result_type == ActorResultType.ASK_CLARIFICATION:
        state.human_review_resume_state = _infer_human_review_resume_state(
            state=state,
            policy_input=policy_input,
        )
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        return

    if policy_input.actor_result_type == ActorResultType.BLOCKED:
        state.human_review_resume_state = _infer_human_review_resume_state(
            state=state,
            policy_input=policy_input,
        )
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        return

    if policy_input.actor_result_type in {
        ActorResultType.INTERMEDIATE_OUTPUT,
    }:
        # Preserve the actor transition: STEP_COMPLETED already advanced the
        # semantic cursor, while INTERMEDIATE_OUTPUT deliberately kept it open.
        return

    if policy_input.actor_result_type == ActorResultType.STEP_COMPLETED:
        # OPA acceptance is the only commit gate for a local materialized result.
        if state.external_operation_proposal is None:
            if state.pending_completion_candidate is None:
                # Recovered legacy state cannot bypass the proposal/commit boundary.
                restore_actor_step_for_more_evidence(state)
                state.current_state = RuntimeState.ACTING
                return
            commit_completion_candidate(state)
            state.current_state = (
                RuntimeState.COMPLETED
                if state.current_plan_step_index is None
                else RuntimeState.READY_NEXT_STEP
            )
        return

    state.current_state = RuntimeState.ACTING


# Route a governed proposal according to canonical intent without recomputing policy.
def _route_authorized_external_operation(
    *,
    state: TaskState,
    governance_decision: GovernanceDecision,
) -> bool:
    proposal = state.external_operation_proposal
    if (
        proposal is None
        or state.current_tool_request is not None
        or proposal.boundary != OperationBoundary.GOVERNED_EXTERNAL
        or governance_decision.directive
        not in {GovernanceDirective.CONTINUE, GovernanceDirective.CONTINUE_READ_ONLY}
    ):
        return False
    if state.execution_intent == ExecutionIntent.PROPOSAL_ONLY:
        commit_terminal_completion_candidate(
            state,
            expected_operation_digest=proposal.payload_digest,
            skip_reason=(
                "Governance accepted the proposal as the terminal local artifact."
            ),
        )
        state.current_external_execution_request = None
        state.current_state = RuntimeState.COMPLETED
        set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.APPROVED_DRAFT,
            summary=(
                "Governance accepted the proposal as a terminal draft; "
                "the user did not request external execution."
            ),
            review_ready=False,
        )
        return True
    if not proposal.execution_candidate:
        state.termination_reason = TerminationReason.GOVERNANCE_INVARIANT_FAILURE
        state.current_state = RuntimeState.ABORTED
        return True
    evaluated_digest = governance_decision.evaluated_operation_digest
    if evaluated_digest != proposal.payload_digest:
        state.current_external_execution_request = None
        state.termination_reason = TerminationReason.GOVERNANCE_INVARIANT_FAILURE
        state.current_state = RuntimeState.ABORTED
        return True
    state.approved_external_operation_digest = evaluated_digest
    state.current_external_execution_request = build_external_execution_request(
        proposal=proposal,
        authorized_payload_digest=evaluated_digest,
        execution_intent=state.execution_intent,
        authorization_source=ExternalExecutionAuthorizationSource.OPA,
        governance_decision_reference=_governance_decision_reference(
            governance_decision
        ),
    )
    state.current_state = RuntimeState.READY_EXTERNAL_EXECUTION
    return True


# Build a stable audit reference from the exact OPA record already stored in state.
def _governance_decision_reference(
    governance_decision: GovernanceDecision,
) -> str:
    return (
        f"{governance_decision.policy_engine_name}:"
        f"{governance_decision.policy_bundle_version}:"
        f"{governance_decision.evaluated_plan_step_id or 'no-step'}:"
        f"{governance_decision.evaluated_operation_digest or 'no-digest'}"
    )


# Invalidate one semantic payload and return audit-safe findings to the actor.
def _apply_action_revision(
    state: TaskState,
    *,
    governance_decision: GovernanceDecision,
) -> None:
    proposal = state.external_operation_proposal
    if proposal is None:
        # A malformed revision directive cannot route an absent payload toward execution.
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        state.human_review_resume_state = RuntimeState.ACTING
        return
    judge_payload = (governance_decision.pillar_results or {}).get("judge", {})
    findings = (
        judge_payload.get("findings", [])
        if isinstance(judge_payload, dict)
        else []
    )
    state.external_operation_revision_count += 1
    state.governance_action_feedback.append(
        GovernanceActionFeedback(
            operation_id=proposal.operation_id,
            invalidated_payload_digest=proposal.payload_digest,
            finding_codes=tuple(
                str(item.get("code"))
                for item in findings
                if isinstance(item, dict) and item.get("code")
            ),
            summary=governance_decision.summary,
            revision_index=state.external_operation_revision_count,
            invalidated_evidence_selection_digest=(
                state.pending_completion_candidate.evidence_selection_digest
                if state.pending_completion_candidate is not None
                else None
            ),
            # Preserve the full typed findings so the actor can correct each one.
            findings=tuple(
                JudgeFindingSnapshot(
                    category=str(item.get("category", "other")),
                    code=str(item.get("code")),
                    severity=str(item.get("severity", "medium")),
                    summary=str(item.get("summary", "")),
                    evidence_refs=tuple(
                        str(ref) for ref in item.get("evidence_refs", [])
                    ),
                    task_requirement_kind=(
                        str(item["task_requirement"].get("kind"))
                        if isinstance(item.get("task_requirement"), dict)
                        and item["task_requirement"].get("kind")
                        else None
                    ),
                    task_requirement_index=(
                        int(item["task_requirement"]["index"])
                        if isinstance(item.get("task_requirement"), dict)
                        and isinstance(item["task_requirement"].get("index"), int)
                        else None
                    ),
                    task_requirement_quote=(
                        str(item["task_requirement"].get("quote"))
                        if isinstance(item.get("task_requirement"), dict)
                        and item["task_requirement"].get("quote")
                        else None
                    ),
                    payload_field=(
                        str(item.get("payload_field"))
                        if item.get("payload_field")
                        else None
                    ),
                    payload_excerpt=(
                        str(item.get("payload_excerpt"))
                        if item.get("payload_excerpt")
                        else None
                    ),
                )
                for item in findings
                if isinstance(item, dict) and item.get("code")
            ),
            judge_summary=(
                str(judge_payload.get("summary"))
                if isinstance(judge_payload, dict) and judge_payload.get("summary")
                else None
            ),
            judge_rationale=(
                str(judge_payload.get("rationale"))
                if isinstance(judge_payload, dict) and judge_payload.get("rationale")
                else None
            ),
        )
    )
    state.approved_external_operation_digest = None
    state.external_operation_proposal = None
    state.current_external_execution_request = None
    if state.actor_result is not None:
        state.actor_result = replace(
            state.actor_result,
            external_operation_proposal=None,
            external_impact=False,
        )
    set_external_communication_phase(
        state,
        phase=ExternalCommunicationPhase.DRAFT_IN_PROGRESS,
        summary="Governance invalidated the current payload and requested a bounded semantic revision.",
        review_ready=False,
    )
    restore_actor_step_for_more_evidence(state)
    state.current_tool_request = None
    state.pending_human_review_request = None
    state.human_review_resume_state = None
    state.current_state = RuntimeState.ACTING

# Apply a validated policy outcome to canonical runtime state.
def _infer_human_review_resume_state(
    *,
    state: TaskState,
    policy_input: PolicyEvaluationInput,
) -> RuntimeState:
    if state.current_tool_request is not None:
        return RuntimeState.READY_TOOL_EXECUTION

    if policy_input.actor_result_type == ActorResultType.ASK_CLARIFICATION:
        return RuntimeState.ACTING

    if policy_input.actor_result_type == ActorResultType.BLOCKED:
        return RuntimeState.ACTING

    if policy_input.actor_result_type == ActorResultType.PROPOSE_ACTION:
        return (
            RuntimeState.READY_TOOL_EXECUTION
            if state.current_tool_request is not None
            else RuntimeState.ACTING
        )

    if state.current_plan_step_index is None:
        return RuntimeState.COMPLETED if state.executed_steps else RuntimeState.PLANNING

    if state.current_state == RuntimeState.READY_NEXT_STEP:
        return RuntimeState.READY_NEXT_STEP

    return RuntimeState.ACTING

__all__ = [
    "_apply_governance_decision",
    "_infer_human_review_resume_state",
    "_route_authorized_external_operation",
    "governance",
]
