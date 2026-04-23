from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from layer1_intake import ExecutionPolicy, IntakeMetadata, TaskSpec

from .state import ExecutedPlanStep, PlanStepStatus, RuntimeFlag, RuntimeState, TaskState
from .tool_contract import ObservedEvidence, ToolExecutionResult, ToolRequest


# CapabilityAccessMode keeps external-read and external-write intent explicit.
class CapabilityAccessMode(str, Enum):
    NONE = "none"
    READ_ONLY = "read_only"
    WRITE = "write"


# ActorResultType names the possible outcomes of one actor step.
class ActorResultType(str, Enum):
    PROPOSE_ACTION = "propose_action"
    INTERMEDIATE_OUTPUT = "intermediate_output"
    FINAL_OUTPUT = "final_output"
    ASK_CLARIFICATION = "ask_clarification"
    BLOCKED = "blocked"
    NEEDS_REPLAN = "needs_replan"


@dataclass(frozen=True)
class ActorPlanStep:
    # The actor receives an immutable copy of each semantic plan step.
    step_id: str
    objective: str
    rationale: str
    expected_outcome: str
    status: PlanStepStatus


@dataclass(frozen=True)
class ActorPastStep:
    # The actor receives a compact history of already executed plan steps.
    step_id: str
    objective: str
    result_type: ActorResultType
    summary: str
    output_text: str | None = None


@dataclass(frozen=True)
class ActorTask:
    # The actor sees the whole plan, past executed steps, and observed evidence.
    task_spec: TaskSpec
    execution_policy: ExecutionPolicy
    intake_metadata: IntakeMetadata
    current_state: RuntimeState
    runtime_flags: tuple[RuntimeFlag, ...]
    plan_steps: tuple[ActorPlanStep, ...]
    past_steps: tuple[ActorPastStep, ...]
    observed_evidence: tuple[ObservedEvidence, ...]
    last_tool_result: ToolExecutionResult | None
    tool_call_count: int
    remaining_tool_calls: int | None
    current_plan_step_index: int
    current_plan_step: ActorPlanStep
    planner_rationale: str | None = None
    planner_name: str | None = None
    planner_prompt_version: str | None = None


@dataclass(frozen=True)
class ActorResult:
    # The actor returns one concrete execution outcome for the current step.
    plan_step_id: str
    result_type: ActorResultType
    summary: str
    capability_mode: CapabilityAccessMode = CapabilityAccessMode.NONE
    external_impact: bool = False
    output_text: str | None = None
    blocking_reason: str | None = None
    tool_request: ToolRequest | None = None


class ActorPort(Protocol):
    # The actor port keeps runtime wiring decoupled from the concrete implementation.
    actor_name: str
    prompt_version: str

    def act(self, *, actor_task: ActorTask) -> ActorResult: ...


# Build the immutable planner-to-actor handoff from the current runtime state.
def build_actor_task(*, task_state: TaskState) -> ActorTask:
    # The actor contract is valid only when the planner has selected one executable step.
    if task_state.current_plan_step_index is None:
        raise ValueError("Cannot build ActorTask without current_plan_step_index.")

    current_index = task_state.current_plan_step_index
    if not 0 <= current_index < len(task_state.plan_steps):
        raise ValueError("current_plan_step_index is out of bounds for plan_steps.")

    if task_state.current_plan_step is None:
        raise ValueError("Cannot build ActorTask without a current plan step.")

    # Freeze the semantic plan so the actor can inspect it without mutating runtime state.
    actor_plan_steps = tuple(
        ActorPlanStep(
            step_id=step.step_id,
            objective=step.objective,
            rationale=step.rationale,
            expected_outcome=step.expected_outcome,
            status=step.status,
        )
        for step in task_state.plan_steps
    )

    # Freeze the executed step history so the actor can reason from prior completions.
    actor_past_steps = tuple(
        ActorPastStep(
            step_id=step.step_id,
            objective=step.objective,
            result_type=step.result_type,
            summary=step.summary,
            output_text=step.output_text,
        )
        for step in task_state.executed_steps
    )

    return ActorTask(
        task_spec=task_state.task_spec.model_copy(deep=True),
        execution_policy=task_state.execution_policy.model_copy(deep=True),
        intake_metadata=task_state.intake_metadata.model_copy(deep=True),
        current_state=task_state.current_state,
        runtime_flags=tuple(task_state.runtime_flags),
        plan_steps=actor_plan_steps,
        past_steps=actor_past_steps,
        observed_evidence=tuple(task_state.observed_evidence),
        last_tool_result=task_state.last_tool_result,
        tool_call_count=task_state.tool_call_count,
        # Keep tool budget explicit while allowing policies with no hard tool-call ceiling.
        remaining_tool_calls=_compute_remaining_tool_calls(task_state=task_state),
        current_plan_step_index=current_index,
        current_plan_step=actor_plan_steps[current_index],
        planner_rationale=task_state.planner_rationale,
        planner_name=task_state.planner_name,
        planner_prompt_version=task_state.planner_prompt_version,
    )


# Convert the optional tool-call budget into a remaining count the actor can reason about.
def _compute_remaining_tool_calls(*, task_state: TaskState) -> int | None:
    max_tool_calls = task_state.execution_policy.budget.max_tool_calls
    if max_tool_calls is None:
        return None

    return max(max_tool_calls - task_state.tool_call_count, 0)


__all__ = [
    "ActorPastStep",
    "ActorPlanStep",
    "ActorPort",
    "ActorResult",
    "ActorResultType",
    "ActorTask",
    "CapabilityAccessMode",
    "build_actor_task",
]
