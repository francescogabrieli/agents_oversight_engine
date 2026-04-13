from __future__ import annotations

from copy import deepcopy
from typing import Any

from layer1_intake import DomainType, IntakeResult

from .state import (
    GovernanceDecision,
    GovernanceDecisionType,
    HumanDecision,
    HumanDecisionType,
    ProposedAction,
    RuntimeState,
    StepRecord,
    TaskState,
)

# Nodes.py contains the business logic of the runtime.
# It does not build the graph; it only transforms TaskState.

def bootstrap_from_intake(intake_result: IntakeResult) -> TaskState:
    # Convert the canonical Layer 1 payload into the first Layer 2 state.
    state = TaskState.from_intake(intake_result)
    state.step_history.append(
        StepRecord(
            step_index=0,
            runtime_state=state.current_state,
            node_name="bootstrap_from_intake",
            summary="Runtime state initialized from layer 1 intake payload.",
        )
    )
    return state


def planner(state: TaskState) -> TaskState:
    # The planner proposes the next action.
    # For now it is deterministic and grounded only in intake data and runtime flags.
    next_state = _copy_state(state)

    if next_state.current_state not in {RuntimeState.PLANNING, RuntimeState.REPLANNING}:
        return _record_step(
            next_state,
            node_name="planner",
            summary="Planner skipped because the runtime is not in a planning state.",
        )

    next_state.step_index += 1
    next_state.proposed_action = _build_planned_action(next_state)

    if "clarification_required" in next_state.runtime_flags:
        next_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        summary = "Planner stopped and deferred to clarification because intake is not executable."
    else:
        next_state.current_state = RuntimeState.PLANNING
        summary = f"Planner proposed action '{next_state.proposed_action.action_name}'."

    return _record_step(
        next_state,
        node_name="planner",
        summary=summary,
    )


def governance_router(state: TaskState) -> TaskState:
    # This node translates the current proposal into a runtime decision.
    # It is deliberately deterministic for now, so the Layer 2 contract is stable
    # before the full governance subgraph is introduced.
    next_state = _copy_state(state)
    next_state.step_index += 1

    if "clarification_required" in next_state.runtime_flags:
        next_state.governance_output = GovernanceDecision(
            decision=GovernanceDecisionType.ASK_USER_CLARIFICATION,
            rationale="The intake payload is underspecified and requires clarification before execution.",
            triggered_rules=["parse_status_requires_clarification"],
        )
        next_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        return _record_step(
            next_state,
            node_name="governance_router",
            summary="Governance requested clarification before execution.",
        )

    if next_state.proposed_action is None:
        next_state.governance_output = GovernanceDecision(
            decision=GovernanceDecisionType.REPLAN,
            rationale="No proposed action is available, so the task must be replanned.",
            triggered_rules=["missing_proposed_action"],
        )
        next_state.current_state = RuntimeState.REPLANNING
        return _record_step(
            next_state,
            node_name="governance_router",
            summary="Governance requested replanning because no action was available.",
        )

    if next_state.proposed_action.action_type == "write":
        next_state.governance_output = GovernanceDecision(
            decision=GovernanceDecisionType.WAIT_HUMAN_REVIEW,
            rationale="The proposed action has write-side effects and must be reviewed before execution.",
            triggered_rules=["write_action_requires_review"],
        )
        next_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        return _record_step(
            next_state,
            node_name="governance_router",
            summary="Governance routed the task to human review before write execution.",
        )

    if next_state.execution_policy.read_only_mode:
        next_state.governance_output = GovernanceDecision(
            decision=GovernanceDecisionType.CONTINUE_READ_ONLY,
            rationale="Execution policy requires read-only behavior for this task.",
            triggered_rules=["execution_policy_read_only_mode"],
        )
        next_state.current_state = RuntimeState.ACTING
        return _record_step(
            next_state,
            node_name="governance_router",
            summary="Governance allowed execution in read-only mode.",
        )

    if "incomplete_intake" in next_state.runtime_flags:
        next_state.governance_output = GovernanceDecision(
            decision=GovernanceDecisionType.REQUEST_MORE_EVIDENCE,
            rationale="The intake is partial, so the runtime should gather more evidence conservatively.",
            triggered_rules=["partial_intake_requires_more_evidence"],
        )
        next_state.current_state = RuntimeState.REPLANNING
        return _record_step(
            next_state,
            node_name="governance_router",
            summary="Governance requested more evidence because intake is partial.",
        )

    next_state.governance_output = GovernanceDecision(
        decision=GovernanceDecisionType.CONTINUE,
        rationale="The task is executable within the current policy and available evidence.",
        triggered_rules=["default_continue"],
    )
    next_state.current_state = RuntimeState.ACTING
    return _record_step(
        next_state,
        node_name="governance_router",
        summary="Governance allowed execution of the current proposed action.",
    )


def tool_gateway(state: TaskState) -> TaskState:
    # This node prepares a structured request for Layer 3.
    # It does not execute tools directly.
    next_state = _copy_state(state)
    next_state.step_index += 1

    if next_state.proposed_action is None:
        return _record_step(
            next_state,
            node_name="tool_gateway",
            summary="Tool gateway skipped because no proposed action is available.",
        )

    next_state.last_tool_result = {
        "status": "tool_request_prepared",
        "action_name": next_state.proposed_action.action_name,
        "action_type": next_state.proposed_action.action_type,
        "payload": next_state.proposed_action.payload,
        "requires_review": bool(
            next_state.governance_output
            and next_state.governance_output.decision
            == GovernanceDecisionType.WAIT_HUMAN_REVIEW
        ),
    }
    next_state.current_state = RuntimeState.OBSERVING

    return _record_step(
        next_state,
        node_name="tool_gateway",
        summary="Tool gateway prepared a structured request for the external Tool Bus.",
    )


def observe_tool_result(state: TaskState) -> TaskState:
    # Tool outputs become evidence that later nodes can reason over.
    next_state = _copy_state(state)
    next_state.step_index += 1

    if next_state.last_tool_result is None:
        return _record_step(
            next_state,
            node_name="observe_tool_result",
            summary="Observation skipped because no tool result is available.",
        )

    evidence_id = f"evidence_{len(next_state.collected_evidence) + 1}"
    next_state.collected_evidence.append(
        {
            "evidence_id": evidence_id,
            "source": "tool_gateway",
            "content": deepcopy(next_state.last_tool_result),
        }
    )
    next_state.current_state = RuntimeState.OBSERVING

    return _record_step(
        next_state,
        node_name="observe_tool_result",
        summary=f"Observed tool output and persisted it as {evidence_id}.",
    )


def replanner(state: TaskState) -> TaskState:
    # Replanning happens after new evidence or governance feedback.
    next_state = _copy_state(state)
    next_state.current_state = RuntimeState.REPLANNING
    next_state.runtime_flags = [flag for flag in next_state.runtime_flags if flag != "incomplete_intake"]
    next_state.step_index += 1
    next_state.proposed_action = ProposedAction(
        action_name="synthesize_or_refine",
        action_type="read",
        rationale="Replanning uses the evidence collected so far to refine the next step conservatively.",
        payload={
            "evidence_count": len(next_state.collected_evidence),
            "goal": next_state.task_spec.goal,
        },
    )

    return _record_step(
        next_state,
        node_name="replanner",
        summary="Replanner generated a follow-up action from the collected evidence.",
    )


def apply_human_decision(state: TaskState) -> TaskState:
    # Apply the human decision after an interrupt/resume cycle.
    next_state = _copy_state(state)
    next_state.step_index += 1

    if next_state.human_decision is None:
        return _record_step(
            next_state,
            node_name="apply_human_decision",
            summary="Human decision node was reached without a decision payload.",
        )

    decision = next_state.human_decision.decision

    if decision == HumanDecisionType.APPROVE:
        next_state.current_state = RuntimeState.ACTING
        if next_state.human_decision.edited_action is not None:
            next_state.proposed_action = next_state.human_decision.edited_action
        return _record_step(
            next_state,
            node_name="apply_human_decision",
            summary="Human reviewer approved the proposed action.",
        )

    if decision == HumanDecisionType.EDIT:
        if next_state.human_decision.edited_action is not None:
            next_state.proposed_action = next_state.human_decision.edited_action
            next_state.current_state = RuntimeState.ACTING
            return _record_step(
                next_state,
                node_name="apply_human_decision",
                summary="Human reviewer edited the proposed action and resumed execution.",
            )

        next_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        next_state.runtime_flags.append("missing_human_edit_payload")
        return _record_step(
            next_state,
            node_name="apply_human_decision",
            summary="Human reviewer selected edit, but no edited action was provided.",
        )

    if decision == HumanDecisionType.REQUEST_MORE_EVIDENCE:
        next_state.current_state = RuntimeState.REPLANNING
        return _record_step(
            next_state,
            node_name="apply_human_decision",
            summary="Human reviewer requested more evidence before continuing.",
        )

    reason = next_state.human_decision.comment or "Execution stopped by human reviewer."
    return abort(next_state, reason=reason, node_name="apply_human_decision")


def finalize(state: TaskState) -> TaskState:
    # Finalize produces the compact runtime output and marks the task as completed.
    next_state = _copy_state(state)
    next_state.step_index += 1
    next_state.current_state = RuntimeState.COMPLETED
    next_state.final_output = {
        "goal": next_state.task_spec.goal,
        "evidence_count": len(next_state.collected_evidence),
        "last_governance_decision": (
            next_state.governance_output.decision.value
            if next_state.governance_output is not None
            else None
        ),
        "termination_reason": next_state.termination_reason,
    }

    return _record_step(
        next_state,
        node_name="finalize",
        summary="Runtime finalized the task output and marked the task as completed.",
    )


def abort(
    state: TaskState,
    *,
    reason: str | None = None,
    node_name: str = "abort",
) -> TaskState:
    # Abort closes the runtime with a structured reason.
    next_state = _copy_state(state)
    next_state.step_index += 1
    next_state.current_state = RuntimeState.ABORTED
    next_state.termination_reason = reason or "Runtime execution aborted."

    return _record_step(
        next_state,
        node_name=node_name,
        summary=f"Runtime aborted execution: {next_state.termination_reason}",
    )


def build_human_review_payload(state: TaskState) -> dict[str, Any]:
    # This payload is what the reviewer should see during HITL.
    return {
        "request_id": state.intake_metadata.request_id,
        "goal": state.task_spec.goal,
        "context": state.task_spec.context,
        "current_state": state.current_state.value,
        "proposed_action": (
            {
                "action_name": state.proposed_action.action_name,
                "action_type": state.proposed_action.action_type,
                "rationale": state.proposed_action.rationale,
                "payload": deepcopy(state.proposed_action.payload),
            }
            if state.proposed_action is not None
            else None
        ),
        "governance_output": (
            {
                "decision": state.governance_output.decision.value,
                "rationale": state.governance_output.rationale,
                "triggered_rules": list(state.governance_output.triggered_rules),
            }
            if state.governance_output is not None
            else None
        ),
        "warnings": list(state.intake_metadata.warnings),
        "missing_information": list(state.intake_metadata.missing_information),
        "evidence_count": len(state.collected_evidence),
    }


def coerce_human_decision(value: HumanDecision | dict[str, Any]) -> HumanDecision:
    # Resume payloads can arrive as dicts from the UI.
    # Normalize them into the canonical HumanDecision model.
    if isinstance(value, HumanDecision):
        return value

    if not isinstance(value, dict):
        raise TypeError("Human review resume payload must be a HumanDecision or dict.")

    decision_value = value.get("decision")
    if isinstance(decision_value, HumanDecisionType):
        decision = decision_value
    elif isinstance(decision_value, str):
        decision = HumanDecisionType(decision_value)
    else:
        raise ValueError("Human review payload must contain a valid decision.")

    edited_action_payload = value.get("edited_action")
    edited_action: ProposedAction | None = None
    if edited_action_payload is not None:
        if isinstance(edited_action_payload, ProposedAction):
            edited_action = edited_action_payload
        elif isinstance(edited_action_payload, dict):
            edited_action = ProposedAction(
                action_name=edited_action_payload["action_name"],
                action_type=edited_action_payload["action_type"],
                rationale=edited_action_payload["rationale"],
                payload=deepcopy(edited_action_payload.get("payload", {})),
            )
        else:
            raise TypeError("edited_action must be a ProposedAction or dict.")

    comment = value.get("comment")
    if comment is not None and not isinstance(comment, str):
        raise TypeError("Human review comment must be a string or null.")

    return HumanDecision(
        decision=decision,
        comment=comment,
        edited_action=edited_action,
    )


def _build_planned_action(state: TaskState) -> ProposedAction:
    # Choose the next action from the current task class and runtime flags.
    if "clarification_required" in state.runtime_flags:
        return ProposedAction(
            action_name="request_clarification",
            action_type="human_input",
            rationale="The intake payload is not specific enough to execute safely.",
            payload={"missing_information": list(state.intake_metadata.missing_information)},
        )

    if state.execution_policy.read_only_mode:
        return ProposedAction(
            action_name="collect_read_only_evidence",
            action_type="read",
            rationale="The execution policy currently allows only non-mutating actions.",
            payload={"goal": state.task_spec.goal},
        )

    if state.task_spec.domain_type == DomainType.EXTERNAL_COMMUNICATION:
        return ProposedAction(
            action_name="draft_external_message",
            action_type="write",
            rationale="The task requires preparing an externally impactful communication.",
            payload={"goal": state.task_spec.goal},
        )

    if state.task_spec.domain_type in {
        DomainType.RESEARCH,
        DomainType.ANALYSIS,
        DomainType.SUMMARIZATION,
        DomainType.DATA_EXTRACTION,
    }:
        return ProposedAction(
            action_name="collect_evidence",
            action_type="read",
            rationale="The next step is to gather or inspect information needed to satisfy the task goal.",
            payload={"goal": state.task_spec.goal, "constraints": list(state.task_spec.constraints)},
        )

    return ProposedAction(
        action_name="progress_task",
        action_type="generic",
        rationale="The runtime will progress the task with a general-purpose next step.",
        payload={"goal": state.task_spec.goal},
    )


def _copy_state(state: TaskState) -> TaskState:
    # Nodes work on a copy to keep updates explicit and predictable.
    return deepcopy(state)


def _record_step(
    state: TaskState,
    *,
    node_name: str,
    summary: str,
) -> TaskState:
    # Every node appends a history record so the run can be replayed later.
    state.step_history.append(
        StepRecord(
            step_index=state.step_index,
            runtime_state=state.current_state,
            node_name=node_name,
            summary=summary,
        )
    )
    return state


__all__ = [
    "abort",
    "apply_human_decision",
    "bootstrap_from_intake",
    "build_human_review_payload",
    "coerce_human_decision",
    "finalize",
    "governance_router",
    "observe_tool_result",
    "planner",
    "replanner",
    "tool_gateway",
]
