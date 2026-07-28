from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

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


# Fingerprint semantic plan content without depending on runtime-owned status.
def semantic_plan_signature(steps: Iterable[PlanStep]) -> str:
    payload = [
        {
            "step_id": step.step_id,
            "objective": step.objective,
            "rationale": step.rationale,
            "expected_outcome": step.expected_outcome,
        }
        for step in steps
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "PlanStepStatus",
    "PlanStep",
    "PlannerDecision",
    "semantic_plan_signature",
]
