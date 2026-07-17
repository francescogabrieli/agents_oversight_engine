from copy import deepcopy
from typing import Callable

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
    PlanStepStatus,
    RuntimeState,
    TaskState,
)

# Nodes.py contains the executable Layer 2 business logic.
# At this stage the runtime performs bootstrap, planning, and the actor/tool loop.
from .actor_service import _set_external_communication_phase
from .lifecycle_service import _append_step_record

# Apply a validated policy outcome to canonical runtime state.
def governance(
    state: TaskState,
    *,
    policy_engine_service: PolicyEnginePort,
    judge_service: JudgePort | None = None,
    behavioral_store_service: BehavioralStorePort | None = None,
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
        except Exception:
            pass
    else:
        try:
            persist_judge_snapshot(
                thread_id=resolved_thread_id,
                task_state=state,
                behavioral_store=behavioral_store_service,
            )
        except Exception:
            pass

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
        state.pending_human_review_request = None
        state.human_review_resume_state = None
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
        _restore_actor_step_for_more_evidence(state)
        state.current_state = RuntimeState.ACTING
        state.current_tool_request = None
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
        ActorResultType.FINAL_OUTPUT,
    }:
        return

    state.current_state = RuntimeState.ACTING

# Apply a validated policy outcome to canonical runtime state.
def _restore_actor_step_for_more_evidence(state: TaskState) -> None:
    actor_result = state.actor_result
    if actor_result is None:
        return

    for index, step in enumerate(state.plan_steps):
        if step.step_id == actor_result.plan_step_id:
            step.status = PlanStepStatus.IN_PROGRESS
            state.current_plan_step_index = index
        elif step.status == PlanStepStatus.IN_PROGRESS:
            step.status = PlanStepStatus.PENDING

    state.executed_steps = [
        step for step in state.executed_steps if step.step_id != actor_result.plan_step_id
    ]
    if state.final_output_text == actor_result.output_text:
        state.final_output_text = None

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
        return RuntimeState.READY_TOOL_EXECUTION

    if state.current_plan_step_index is None:
        return RuntimeState.COMPLETED if state.executed_steps else RuntimeState.PLANNING

    if state.current_state == RuntimeState.READY_NEXT_STEP:
        return RuntimeState.READY_NEXT_STEP

    return RuntimeState.ACTING

__all__ = [
    "governance",
    "_apply_governance_decision",
    "_restore_actor_step_for_more_evidence",
    "_infer_human_review_resume_state"
]
