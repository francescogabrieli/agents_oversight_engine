from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from layer1_intake import (
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    TaskSpec,
)


# Layer 2 currently starts and stops at bootstrap.
# This file only keeps the state required to model the Layer 1 -> Layer 2 handoff.
class RuntimeState(str, Enum):
    PLANNING = "planning"
    WAITING_HUMAN_REVIEW = "waiting_human_review"
    BOOTSTRAPPED = "bootstrapped"


class RuntimeFlag(str, Enum):
    CLARIFICATION_REQUIRED = "clarification_required"
    INCOMPLETE_INTAKE = "incomplete_intake"
    READ_ONLY_EXECUTION = "read_only_execution"
    HIGH_SENSITIVITY = "high_sensitivity"


@dataclass
class StepRecord:
    step_index: int
    runtime_state: RuntimeState
    node_name: str
    summary: str


@dataclass
class TaskState:
    # Direct handoff from Layer 1
    intake_payload: IntakeResult
    task_spec: TaskSpec
    execution_policy: ExecutionPolicy
    intake_metadata: IntakeMetadata

    # Bootstrap outcome
    current_state: RuntimeState
    step_index: int = 0
    step_history: list[StepRecord] = field(default_factory=list)
    runtime_flags: list[RuntimeFlag] = field(default_factory=list)
    bootstrap_completed: bool = False

    @classmethod
    def from_intake(cls, intake_result: IntakeResult) -> "TaskState":
        parse_status = intake_result.metadata.parse_status
        runtime_flags: list[RuntimeFlag] = []

        if parse_status == ParseStatus.NEEDS_CLARIFICATION:
            current_state = RuntimeState.WAITING_HUMAN_REVIEW
            runtime_flags.append(RuntimeFlag.CLARIFICATION_REQUIRED)
        elif parse_status == ParseStatus.PARTIAL:
            current_state = RuntimeState.PLANNING
            runtime_flags.append(RuntimeFlag.INCOMPLETE_INTAKE)
        else:
            current_state = RuntimeState.PLANNING

        if intake_result.structured_task.execution_policy.read_only_mode:
            runtime_flags.append(RuntimeFlag.READ_ONLY_EXECUTION)

        if intake_result.structured_task.task_spec.sensitivity in {
            SensitivityLevel.HIGH,
            SensitivityLevel.CRITICAL,
        }:
            runtime_flags.append(RuntimeFlag.HIGH_SENSITIVITY)

        return cls(
            intake_payload=intake_result,
            task_spec=intake_result.structured_task.task_spec,
            execution_policy=intake_result.structured_task.execution_policy,
            intake_metadata=intake_result.metadata,
            current_state=current_state,
            runtime_flags=runtime_flags,
        )


__all__ = [
    "RuntimeFlag",
    "RuntimeState",
    "StepRecord",
    "TaskState",
]
