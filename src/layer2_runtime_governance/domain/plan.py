from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Canonical domain contract extracted from the legacy state facade.
class PlanStepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


# Canonical domain contract extracted from the legacy state facade.
@dataclass
class PlanStep:
    # Planner output stays semantic so actor can later choose the concrete action.
    step_id: str
    objective: str
    rationale: str
    expected_outcome: str
    status: PlanStepStatus = PlanStepStatus.PENDING


# Canonical domain contract extracted from the legacy state facade.
@dataclass
class PlannerDecision:
    # The planner returns a typed semantic planning snapshot plus traceability metadata.
    plan_steps: list[PlanStep]
    rationale: str
    planner_name: str
    prompt_version: str


__all__ = [
    "PlanStepStatus",
    "PlanStep",
    "PlannerDecision"
]
