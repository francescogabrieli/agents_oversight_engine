from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from layer1_intake import ExecutionPolicy, IntakeMetadata, TaskSpec

from .state import PlanStepStatus, RuntimeFlag, RuntimeState, TaskState


# ActorResultType keeps execution outcomes separate from governance decisions.
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
class ActorTask:
    # The actor sees the whole plan as context, but acts only on the current step.
    task_spec: TaskSpec
    execution_policy: ExecutionPolicy
    intake_metadata: IntakeMetadata
    current_state: RuntimeState
    runtime_flags: tuple[RuntimeFlag, ...]
    plan_steps: tuple[ActorPlanStep, ...]
    current_plan_step_index: int
    current_plan_step: ActorPlanStep
    planner_rationale: str | None = None
    planner_name: str | None = None
    planner_prompt_version: str | None = None


@dataclass(frozen=True)
class ActorResult:
    # The actor returns an operational outcome without rewriting the plan.
    plan_step_id: str
    result_type: ActorResultType
    summary: str
    output_text: str | None = None
    blocking_reason: str | None = None


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

    return ActorTask(
        task_spec=task_state.task_spec.model_copy(deep=True),
        execution_policy=task_state.execution_policy.model_copy(deep=True),
        intake_metadata=task_state.intake_metadata.model_copy(deep=True),
        current_state=task_state.current_state,
        runtime_flags=tuple(task_state.runtime_flags),
        plan_steps=actor_plan_steps,
        current_plan_step_index=current_index,
        current_plan_step=actor_plan_steps[current_index],
        planner_rationale=task_state.planner_rationale,
        planner_name=task_state.planner_name,
        planner_prompt_version=task_state.planner_prompt_version,
    )


__all__ = [
    "ActorPlanStep",
    "ActorPort",
    "ActorResult",
    "ActorResultType",
    "ActorTask",
    "build_actor_task",
]
