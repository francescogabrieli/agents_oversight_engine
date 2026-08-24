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

    # Keep the planner-owned burden usable as a deterministic governance contract.
    def __post_init__(self) -> None:
        if self.min_independent_sources < 1:
            raise ValueError("EvidenceBurden.min_independent_sources must be at least 1.")
        if self.freshness_window_days is not None and self.freshness_window_days < 1:
            raise ValueError("EvidenceBurden.freshness_window_days must be at least 1 when set.")


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

    # Enforce the declared proof obligation at the canonical semantic-plan boundary.
    def __post_init__(self) -> None:
        validate_plan_step_evidence_contract(
            produces_factual_claims=self.produces_factual_claims,
            evidence_burden=self.evidence_burden,
        )


# Enforce the one planner invariant shared by typed provider payloads and runtime plans.
def validate_plan_step_evidence_contract(
    *,
    produces_factual_claims: bool,
    evidence_burden: object | None,
) -> None:
    if produces_factual_claims and evidence_burden is None:
        raise ValueError(
            "Plan steps that produce factual claims must declare an evidence_burden."
        )


# Revalidate mutable plan-step objects before they enter an executable runtime trajectory.
def validate_plan_steps_evidence_contract(steps: Iterable[PlanStep]) -> None:
    for step in steps:
        validate_plan_step_evidence_contract(
            produces_factual_claims=step.produces_factual_claims,
            evidence_burden=step.evidence_burden,
        )


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
    "validate_plan_step_evidence_contract",
    "validate_plan_steps_evidence_contract",
]
