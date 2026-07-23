from layer1_intake import DomainType

from ..actor_contract import ActorPort, ActorResult, ActorResultType, build_actor_task
from ..domain import (
    ExecutedPlanStep,
    ExternalCommunicationDraft,
    ExternalCommunicationPhase,
    PlanStepStatus,
    RuntimeState,
    StepValidationDecision,
    TaskState,
)

# Nodes.py contains the executable Layer 2 business logic.
# At this stage the runtime performs bootstrap, planning, and the actor/tool loop.
from .lifecycle_service import _append_step_record

# Apply actor progress while preserving typed runtime invariants.
def actor(state: TaskState, *, actor_service: ActorPort) -> TaskState:
    if state.current_state not in {
        RuntimeState.PLANNING,
        RuntimeState.ACTING,
        RuntimeState.READY_NEXT_STEP,
    } or state.current_plan_step is None:
        _append_step_record(
            state,
            node_name="actor",
            summary="Actor skipped because there is no executable plan step in an actor-ready state.",
        )
        return state

    state.actor_turn_count += 1

    actor_task = build_actor_task(task_state=state)
    actor_result = actor_service.act(actor_task=actor_task)

    # A successful actor turn creates a new action candidate. The previous immutable
    # proposal remains in audit history but cannot govern or authorize the new result.
    state.external_operation_proposal = None
    state.approved_external_operation_digest = None
    state.actor_result = actor_result
    state.actor_name = actor_service.actor_name
    state.actor_prompt_version = actor_service.prompt_version
    state.current_tool_request = (
        actor_result.tool_request if actor_result.result_type == ActorResultType.PROPOSE_ACTION else None
    )

    _record_actor_execution(state, actor_result=actor_result)
    state.current_state = _map_actor_result_to_runtime_state(state, actor_result=actor_result)

    _append_step_record(
        state,
        node_name="actor",
        summary="Actor executed the current semantic step and returned one typed execution outcome.",
    )
    return state

# Apply actor progress while preserving typed runtime invariants.
def _record_actor_execution(state: TaskState, *, actor_result: ActorResult) -> None:
    current_step = state.current_plan_step
    if current_step is None:
        raise ValueError("Cannot record actor execution without a current plan step.")

    if actor_result.plan_step_id != current_step.step_id:
        raise ValueError("actor_result.plan_step_id does not match the current plan step.")

    _update_external_communication_after_actor_result(state, actor_result=actor_result)

    if actor_result.scratchpad_entry is not None:
        # Store summarized working memory before any routing decision changes the state.
        state.scratchpad.append(actor_result.scratchpad_entry)

    if actor_result.step_validation is not None:
        if actor_result.step_validation.plan_step_id != current_step.step_id:
            raise ValueError("step_validation.plan_step_id does not match the current plan step.")
        state.step_validations.append(actor_result.step_validation)

    if actor_result.result_type in {
        ActorResultType.ASK_CLARIFICATION,
        ActorResultType.BLOCKED,
        ActorResultType.NEEDS_REPLAN,
    }:
        current_step.status = PlanStepStatus.BLOCKED
        state.current_tool_request = None
        return

    if actor_result.result_type == ActorResultType.PROPOSE_ACTION:
        if actor_result.tool_request is None:
            raise ValueError("propose_action requires tool_request.")
        # Tool proposals keep the current semantic step alive until observation returns.
        current_step.status = PlanStepStatus.IN_PROGRESS
        return

    if actor_result.step_validation is None:
        raise ValueError("Completing a plan step requires step_validation.")

    validation = actor_result.step_validation

    if validation.next_action in {
        StepValidationDecision.CONTINUE_ACTING,
        StepValidationDecision.REQUEST_MORE_EVIDENCE,
    }:
        # A local output can be useful progress without satisfying the semantic objective.
        current_step.status = PlanStepStatus.IN_PROGRESS
        return

    if validation.next_action == StepValidationDecision.ASK_CLARIFICATION:
        current_step.status = PlanStepStatus.BLOCKED
        state.current_tool_request = None
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        return

    if validation.next_action == StepValidationDecision.NEEDS_REPLAN:
        current_step.status = PlanStepStatus.BLOCKED
        state.current_tool_request = None
        state.current_state = RuntimeState.REPLANNING
        return

    if not (
        validation.next_action == StepValidationDecision.COMPLETE_STEP
        and validation.objective_satisfied
        and validation.evidence_sufficient
    ):
        # Invalid or low-confidence completion attempts are treated as continued work.
        current_step.status = PlanStepStatus.IN_PROGRESS
        return

    current_step.status = PlanStepStatus.COMPLETED
    state.executed_steps.append(
        ExecutedPlanStep(
            step_id=current_step.step_id,
            objective=current_step.objective,
            result_type=actor_result.result_type,
            summary=actor_result.summary,
            output_text=actor_result.output_text,
        )
    )

    # Once a step is completed, the last tool result becomes historical context rather than live intent.
    state.current_tool_request = None
    state.last_tool_result = None

    if actor_result.result_type == ActorResultType.FINAL_OUTPUT:
        if state.task_spec.domain_type == DomainType.EXTERNAL_COMMUNICATION:
            state.final_output_text = _build_external_communication_draft_body(state)
        else:
            state.final_output_text = actor_result.output_text
        state.current_plan_step_index = None
        return

    _advance_to_next_plan_step(state)
    # Promote the last completed step output into the canonical final output slot.
    if state.current_plan_step_index is None and actor_result.output_text:
        if state.task_spec.domain_type == DomainType.EXTERNAL_COMMUNICATION:
            state.final_output_text = _build_external_communication_draft_body(state)
        else:
            state.final_output_text = actor_result.output_text

# Apply actor progress while preserving typed runtime invariants.
def _update_external_communication_after_actor_result(
    state: TaskState,
    *,
    actor_result: ActorResult,
) -> None:
    if state.task_spec.domain_type != DomainType.EXTERNAL_COMMUNICATION:
        return

    if actor_result.result_type in {
        ActorResultType.ASK_CLARIFICATION,
        ActorResultType.NEEDS_REPLAN,
    }:
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.EVIDENCE_COLLECTION,
            summary="The external communication branch needs more evidence or a revised plan before drafting.",
            review_ready=False,
        )
        return

    if actor_result.result_type == ActorResultType.BLOCKED:
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.EVIDENCE_COLLECTION,
            summary="The external communication branch hit a recoverable runtime block and remains in recovery.",
            review_ready=False,
        )
        return

    if (
        state.external_communication is not None
        and state.external_communication.phase == ExternalCommunicationPhase.APPROVED_FOR_EXECUTION
    ):
        return

    if actor_result.output_text and actor_result.output_text.strip():
        review_ready = _external_communication_output_is_review_ready(
            state,
            actor_result=actor_result,
        )
        _merge_external_communication_draft(
            state,
            output_text=actor_result.output_text.strip(),
            typed_draft=actor_result.external_communication_draft,
            review_ready=review_ready,
        )
        _set_external_communication_phase(
            state,
            phase=(
                ExternalCommunicationPhase.DRAFT_PROPOSAL
                if review_ready
                else ExternalCommunicationPhase.DRAFT_IN_PROGRESS
            ),
            summary=(
                "A review-ready external draft proposal exists and remains inside the runtime boundary."
                if review_ready
                else "A partial external draft is being assembled inside the runtime boundary."
            ),
            review_ready=review_ready,
        )

# Apply actor progress while preserving typed runtime invariants.
def _set_external_communication_phase(
    state: TaskState,
    *,
    phase: ExternalCommunicationPhase,
    summary: str,
    review_ready: bool | None = None,
) -> None:
    if state.external_communication is None:
        return
    state.external_communication.phase = phase
    if review_ready is not None:
        state.external_communication.review_ready = review_ready
    state.external_communication.last_transition_summary = summary

# Apply actor progress while preserving typed runtime invariants.
def _external_communication_output_is_review_ready(
    state: TaskState,
    *,
    actor_result: ActorResult,
) -> bool:
    current_step = state.current_plan_step
    validation = actor_result.step_validation
    if current_step is None or validation is None:
        return False

    if actor_result.result_type not in {
        ActorResultType.INTERMEDIATE_OUTPUT,
        ActorResultType.FINAL_OUTPUT,
    }:
        return False

    if not (
        validation.next_action == StepValidationDecision.COMPLETE_STEP
        and validation.objective_satisfied
        and validation.evidence_sufficient
    ):
        return False

    if state.current_plan_step_index == len(state.plan_steps) - 1:
        return True

    semantic_text = " ".join(
        filter(
            None,
            [
                current_step.objective,
                current_step.expected_outcome,
                current_step.rationale,
            ],
        )
    ).lower()
    readiness_markers = (
        "final draft",
        "finalized email draft",
        "ready to send",
        "suitable for immediate sending",
        "complete email draft",
        "full email draft",
        "compose the email",
    )
    return any(marker in semantic_text for marker in readiness_markers)

# Apply actor progress while preserving typed runtime invariants.
def _merge_external_communication_draft(
    state: TaskState,
    *,
    output_text: str,
    typed_draft: ExternalCommunicationDraft | None = None,
    review_ready: bool,
) -> None:
    if state.external_communication is None:
        return

    current_draft = state.external_communication.draft
    metadata = dict(current_draft.metadata) if current_draft is not None else {}
    if typed_draft is not None:
        metadata.update(typed_draft.metadata)
    if "source" not in metadata:
        metadata["source"] = "actor_output"
    metadata.setdefault("content_format", "markdown")
    metadata["review_ready"] = review_ready

    state.external_communication.draft = ExternalCommunicationDraft(
        channel=(
            typed_draft.channel
            if typed_draft is not None
            else current_draft.channel
            if current_draft is not None
            else "external_communication"
        ),
        recipient=(
            typed_draft.recipient
            if typed_draft is not None
            else current_draft.recipient
            if current_draft is not None
            else None
        ),
        subject=(
            typed_draft.subject
            if typed_draft is not None
            else current_draft.subject
            if current_draft is not None
            else None
        ),
        body=_build_external_communication_draft_body(
            state,
            current_output_text=output_text,
        ),
        metadata=metadata,
    )

# Apply actor progress while preserving typed runtime invariants.
def _build_external_communication_draft_body(
    state: TaskState,
    *,
    current_output_text: str | None = None,
) -> str | None:
    current_fragment = (
        current_output_text.strip()
        if current_output_text is not None and current_output_text.strip()
        else None
    )
    if current_fragment is not None:
        return current_fragment

    for step in reversed(state.executed_steps):
        if step.output_text and step.output_text.strip():
            return step.output_text.strip()

    if state.external_communication is not None and state.external_communication.draft is not None:
        draft_body = state.external_communication.draft.body
        if draft_body and draft_body.strip():
            return draft_body.strip()

    if not state.executed_steps:
        return None
    return None

# Apply actor progress while preserving typed runtime invariants.
def _advance_to_next_plan_step(state: TaskState) -> None:
    current_index = state.current_plan_step_index
    if current_index is None:
        return

    for next_index in range(current_index + 1, len(state.plan_steps)):
        next_step = state.plan_steps[next_index]
        if next_step.status == PlanStepStatus.PENDING:
            next_step.status = PlanStepStatus.IN_PROGRESS
            state.current_plan_step_index = next_index
            return

    state.current_plan_step_index = None

# Apply actor progress while preserving typed runtime invariants.
def _map_actor_result_to_runtime_state(
    state: TaskState,
    *,
    actor_result: ActorResult,
) -> RuntimeState:
    if actor_result.result_type == ActorResultType.NEEDS_REPLAN:
        return RuntimeState.REPLANNING

    if actor_result.result_type in {
        ActorResultType.ASK_CLARIFICATION,
    }:
        return RuntimeState.WAITING_HUMAN_REVIEW

    if actor_result.result_type == ActorResultType.BLOCKED:
        return RuntimeState.ACTING

    if actor_result.result_type == ActorResultType.PROPOSE_ACTION:
        return RuntimeState.READY_TOOL_EXECUTION

    if actor_result.step_validation is not None:
        # StepValidation is the runtime-facing self-check that decides whether to continue,
        # escalate, replan, or advance after local actor output.
        if actor_result.step_validation.next_action in {
            StepValidationDecision.CONTINUE_ACTING,
            StepValidationDecision.REQUEST_MORE_EVIDENCE,
        }:
            return RuntimeState.ACTING
        if actor_result.step_validation.next_action == StepValidationDecision.ASK_CLARIFICATION:
            return RuntimeState.WAITING_HUMAN_REVIEW
        if actor_result.step_validation.next_action == StepValidationDecision.NEEDS_REPLAN:
            return RuntimeState.REPLANNING
        if actor_result.step_validation.next_action == StepValidationDecision.COMPLETE_STEP:
            if (
                actor_result.step_validation.objective_satisfied
                and actor_result.step_validation.evidence_sufficient
                and state.current_plan_step_index is None
            ):
                return RuntimeState.COMPLETED
            if (
                actor_result.step_validation.objective_satisfied
                and actor_result.step_validation.evidence_sufficient
            ):
                return RuntimeState.READY_NEXT_STEP
            return RuntimeState.ACTING

    if actor_result.result_type == ActorResultType.FINAL_OUTPUT:
        return RuntimeState.COMPLETED

    if state.current_plan_step_index is None:
        return RuntimeState.COMPLETED

    return RuntimeState.READY_NEXT_STEP

__all__ = [
    "actor",
    "_record_actor_execution",
    "_update_external_communication_after_actor_result",
    "_set_external_communication_phase",
    "_external_communication_output_is_review_ready",
    "_merge_external_communication_draft",
    "_build_external_communication_draft_body",
    "_advance_to_next_plan_step",
    "_map_actor_result_to_runtime_state"
]
