from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from langgraph.types import interrupt

from .actor_contract import ActorResultType
from .oversight import OversightAssessment
from .state import (
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewRequest,
    RuntimeFlag,
    RuntimeState,
    PlanStepStatus,
    StepRecord,
    TaskState,
)


# Build the canonical human review request from the current runtime state.
def build_human_review_request(*, state: TaskState, thread_id: str) -> HumanReviewRequest:
    current_plan_step = state.current_plan_step
    governance_decision = state.last_governance_decision
    oversight_assessment = state.last_oversight_assessment
    resume_state = _resolve_resume_state(state)

    reason_codes: list[str] = []
    if governance_decision is not None:
        reason_codes.extend(code.value for code in governance_decision.reason_codes)
    if oversight_assessment is not None:
        reason_codes.extend(code.value for code in oversight_assessment.reason_codes)
    if not reason_codes:
        reason_codes.append("human_review_required")

    return HumanReviewRequest(
        request_id=_build_request_id(state=state, thread_id=thread_id),
        thread_id=thread_id,
        current_state=state.current_state,
        resume_state=resume_state,
        current_plan_step_id=current_plan_step.step_id if current_plan_step is not None else None,
        current_plan_step_objective=(
            current_plan_step.objective if current_plan_step is not None else None
        ),
        actor_result_type=state.actor_result.result_type if state.actor_result is not None else None,
        current_tool_request=state.current_tool_request,
        governance_directive=(
            governance_decision.directive if governance_decision is not None else None
        ),
        governance_summary=governance_decision.summary if governance_decision is not None else None,
        oversight_summary=(
            oversight_assessment.summary if oversight_assessment is not None else None
        ),
        reason_codes=tuple(reason_codes),
        allowed_decisions=tuple(HumanReviewDecisionType),
        review_summary=_build_review_summary(
            governance_decision=governance_decision,
            oversight_assessment=oversight_assessment,
            resume_state=resume_state,
        ),
    )


# Route the interrupt payload through the LangGraph HITL pause/resume mechanism.
def human_review(state: TaskState, *, thread_id: str) -> TaskState:
    runtime_state = deepcopy(state)

    if runtime_state.pending_human_review_request is None:
        runtime_state.pending_human_review_request = build_human_review_request(
            state=runtime_state,
            thread_id=thread_id,
        )

    pending_request = runtime_state.pending_human_review_request
    if pending_request is None:
        raise ValueError("Human review requested without a pending review payload.")

    # The interrupt payload must be JSON-safe so dashboard code can consume it directly.
    decision_payload = interrupt(_to_jsonable(pending_request))
    human_review_decision = _coerce_human_review_decision(
        decision_payload,
        expected_request_id=pending_request.request_id,
    )
    _apply_human_review_decision(runtime_state, human_review_decision)

    runtime_state.human_review_resume_state = pending_request.resume_state
    runtime_state.last_human_review_request = pending_request
    runtime_state.pending_human_review_request = None
    runtime_state.last_human_review_decision = human_review_decision
    runtime_state.human_review_history.append(human_review_decision)
    runtime_state.step_index += 1
    runtime_state.step_history.append(
        StepRecord(
            step_index=runtime_state.step_index,
            runtime_state=runtime_state.current_state,
            node_name="human_review",
            summary=(
                "Human review resumed the runtime with typed decision "
                f"'{human_review_decision.decision_type.value}'."
            ),
        )
    )
    return runtime_state


# Convert a review decision payload back into the canonical typed decision model.
def _coerce_human_review_decision(
    payload: Any,
    *,
    expected_request_id: str,
) -> HumanReviewDecision:
    if isinstance(payload, HumanReviewDecision):
        decision = payload
    elif isinstance(payload, dict):
        decision_type_value = payload.get("decision_type", payload.get("decision"))
        if decision_type_value is None:
            raise ValueError("Human review resume payload is missing decision_type.")

        decision = HumanReviewDecision(
            request_id=_extract_request_id(payload),
            decision_type=_parse_decision_type(decision_type_value),
            note=_optional_text(payload.get("note")),
            clarification_text=_optional_text(
                payload.get("clarification_text", payload.get("clarification"))
            ),
            reviewer_id=_optional_text(payload.get("reviewer_id")),
        )
    else:
        raise ValueError("Human review resume payload must be a decision dict or dataclass.")

    if decision.request_id != expected_request_id:
        raise ValueError("Human review resume payload request_id does not match the pending review.")

    return decision


# Apply the typed human decision to runtime state without mutating the review payload itself.
def _apply_human_review_decision(
    state: TaskState,
    human_review_decision: HumanReviewDecision,
) -> None:
    pending_request = state.pending_human_review_request
    if pending_request is None:
        raise ValueError("Cannot apply a human review decision without a pending request.")

    if human_review_decision.request_id != pending_request.request_id:
        raise ValueError("Human review decision request_id does not match the pending request.")

    if human_review_decision.decision_type == HumanReviewDecisionType.APPROVE_CONTINUE:
        state.current_state = pending_request.resume_state
        return

    if human_review_decision.decision_type == HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY:
        if not state.execution_policy.read_only_mode:
            state.execution_policy = state.execution_policy.model_copy(
                update={"read_only_mode": True}
            )
        if RuntimeFlag.READ_ONLY_EXECUTION not in state.runtime_flags:
            state.runtime_flags.append(RuntimeFlag.READ_ONLY_EXECUTION)
        state.current_state = pending_request.resume_state
        return

    if human_review_decision.decision_type == HumanReviewDecisionType.REQUEST_MORE_EVIDENCE:
        _restore_actor_step_for_more_evidence(state)
        state.current_tool_request = None
        state.current_state = RuntimeState.ACTING
        return

    if human_review_decision.decision_type == HumanReviewDecisionType.REPLAN:
        state.current_tool_request = None
        state.current_state = RuntimeState.REPLANNING
        return

    if human_review_decision.decision_type == HumanReviewDecisionType.ASK_CLARIFICATION:
        if RuntimeFlag.CLARIFICATION_REQUIRED not in state.runtime_flags:
            state.runtime_flags.append(RuntimeFlag.CLARIFICATION_REQUIRED)
        state.current_tool_request = None
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        return

    if human_review_decision.decision_type == HumanReviewDecisionType.ABORT:
        state.current_tool_request = None
        state.last_tool_result = None
        state.current_state = RuntimeState.ABORTED
        return

    raise ValueError(f"Unsupported human review decision: {human_review_decision.decision_type}")


# Build a stable request id so replay and resume stay aligned across checkpoints.
def _build_request_id(*, state: TaskState, thread_id: str) -> str:
    current_step = state.current_plan_step.step_id if state.current_plan_step is not None else "no_step"
    return f"{thread_id}:human_review:{state.step_index}:{current_step}"


# Resolve the runtime state the graph should return to after approval.
def _resolve_resume_state(state: TaskState) -> RuntimeState:
    if state.human_review_resume_state is not None:
        return state.human_review_resume_state

    if state.current_tool_request is not None:
        return RuntimeState.READY_TOOL_EXECUTION

    if state.actor_result is not None and state.actor_result.result_type == ActorResultType.NEEDS_REPLAN:
        return RuntimeState.REPLANNING

    if state.current_plan_step_index is None:
        return RuntimeState.COMPLETED if state.executed_steps else RuntimeState.PLANNING

    if state.actor_result is not None and state.actor_result.result_type in {
        ActorResultType.INTERMEDIATE_OUTPUT,
        ActorResultType.FINAL_OUTPUT,
    }:
        if state.current_plan_step_index < len(state.plan_steps) - 1:
            return RuntimeState.READY_NEXT_STEP
        return RuntimeState.COMPLETED

    return RuntimeState.ACTING


# Summarize the review for trace and dashboard display.
def _build_review_summary(
    *,
    governance_decision: Any,
    oversight_assessment: OversightAssessment | None,
    resume_state: RuntimeState,
) -> str | None:
    parts: list[str] = []
    if governance_decision is not None:
        parts.append(governance_decision.summary)
    if oversight_assessment is not None and oversight_assessment.summary:
        parts.append(oversight_assessment.summary)
    parts.append(f"Approval would resume to '{resume_state.value}'.")
    return " ".join(parts) if parts else None


# Convert a review payload into JSON-safe primitives for interrupt() and tracing.
def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


# Parse decision enum values while failing closed on malformed resume payloads.
def _parse_decision_type(value: Any) -> HumanReviewDecisionType:
    if isinstance(value, HumanReviewDecisionType):
        return value
    if not isinstance(value, str):
        raise ValueError("Human review decision_type must be a string.")

    try:
        return HumanReviewDecisionType(value)
    except ValueError as exc:
        raise ValueError(f"Unknown human review decision_type: {value}") from exc


# Recover the request id from a review resume payload.
def _extract_request_id(payload: dict[str, Any]) -> str:
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("Human review resume payload is missing request_id.")
    return " ".join(request_id.strip().split())


# Normalize optional free-text fields before storing them in TaskState.
def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Human review text fields must be strings.")
    cleaned = " ".join(value.strip().split())
    return cleaned or None


# Reuse the existing step restoration helper without duplicating runtime semantics.
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
