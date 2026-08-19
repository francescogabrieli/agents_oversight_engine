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
    SKIPPED = "skipped"


# Canonical domain contract extracted from the legacy state facade.
@dataclass(frozen=True)
class EvidenceBurden:
    min_independent_sources: int = 1
    require_retrieved_content: bool = True
    freshness_window_days: int | None = None


# Canonical domain contract extracted from the legacy state facade.
@dataclass
class PlanStep:
    # Planner output stays semantic so actor can later choose the concrete action.
    step_id: str
    objective: str
    rationale: str
    expected_outcome: str
    status: PlanStepStatus = PlanStepStatus.PENDING
    produces_factual_claims: bool = False
    evidence_burden: EvidenceBurden | None = None


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
            "produces_factual_claims": step.produces_factual_claims,
            "evidence_burden": (
                {
                    "min_independent_sources": step.evidence_burden.min_independent_sources,
                    "require_retrieved_content": step.evidence_burden.require_retrieved_content,
                    "freshness_window_days": step.evidence_burden.freshness_window_days,
                }
                if step.evidence_burden is not None
                else None
            ),
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
    "EvidenceBurden",
    "PlanStep",
    "PlannerDecision",
    "semantic_plan_signature",
]
