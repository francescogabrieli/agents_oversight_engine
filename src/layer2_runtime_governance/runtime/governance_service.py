from copy import deepcopy
from dataclasses import replace
from typing import Callable

from layer1_intake import ExecutionIntent

from ..behavioral import (
    BehavioralStorePort,
    persist_behavioral_runtime_snapshot,
    persist_judge_snapshot,
)
from ..actor_contract import ActorResultType
from ..governance_components import (
    GovernanceDecision,
    GovernanceDirective,
    PolicyEnginePort,
    PolicyEvaluationInput,
    evaluate_opa_first_governance,
)
from ..judge import JudgePort
from ..hitl import build_human_review_request
from ..domain import (
    ExternalCommunicationPhase,
    RuntimeState,
    TaskState,
)
from ..tools import ToolGatewayConfig

# Nodes.py contains the executable Layer 2 business logic.
# At this stage the runtime performs bootstrap, planning, and the actor/tool loop.
from .actor_service import (
    _set_external_communication_phase,
    commit_completion_candidate,
    commit_terminal_completion_candidate,
)
from .lifecycle_service import _append_step_record
from ..domain.step_recovery import restore_actor_step_for_more_evidence
from ..external_operations import (
    ExternalExecutionAuthorizationSource,
    GovernanceActionFeedback,
    JudgeFindingSnapshot,
    OperationBoundary,
    build_external_execution_request,
)

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
        except Exception as exc:
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
        except Exception as exc:
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
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.BLOCKED,
            summary="Governance denied the external communication branch.",
            review_ready=False,
        )
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
        _set_external_communication_phase(
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
        _set_external_communication_phase(
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
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.EVIDENCE_COLLECTION,
            summary="Governance required more evidence before the external communication draft can progress.",
            review_ready=False,
        )
        state.pending_human_review_request = None
        state.human_review_resume_state = None
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
        _set_external_communication_phase(
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
        state.current_state = RuntimeState.ABORTED
        return True
    evaluated_digest = governance_decision.evaluated_operation_digest
    if evaluated_digest != proposal.payload_digest:
        state.current_external_execution_request = None
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
    _set_external_communication_phase(
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
    "governance",
    "_apply_governance_decision",
    "_infer_human_review_resume_state",
    "_route_authorized_external_operation",
]
