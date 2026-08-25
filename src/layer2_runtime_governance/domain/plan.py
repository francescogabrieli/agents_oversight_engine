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
    produces_governed_external_effect_proposal: bool = False

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


# Require an explicit terminal proposal step whenever the intake requests execution.
def validate_external_effect_plan_contract(
    *,
    steps: Iterable[PlanStep],
    execution_intent: str | Enum,
) -> None:
    step_list = list(steps)
    intent_value = (
        execution_intent.value
        if isinstance(execution_intent, Enum)
        else execution_intent
    )
    if intent_value not in {"proposal_only", "execute_after_governance"}:
        raise ValueError(f"Unsupported execution intent: {intent_value!r}.")
    marked_indexes = [
        index
        for index, step in enumerate(step_list)
        if step.produces_governed_external_effect_proposal
    ]

    if intent_value == "execute_after_governance":
        if len(marked_indexes) != 1:
            raise ValueError(
                "An execute-after-governance plan must declare exactly one "
                "governed external-effect proposal step."
            )
        if marked_indexes[0] != len(step_list) - 1:
            raise ValueError(
                "The governed external-effect proposal must be the terminal semantic step."
            )
        return

    if marked_indexes:
        raise ValueError(
            "A proposal-only plan cannot declare a governed external-effect proposal step."
        )


# Derive the strongest remaining proof dimensions without assigning them to one step.
def derive_evidence_obligation_floor(
    steps: Iterable[PlanStep],
) -> EvidenceBurden | None:
    step_list = list(steps)
    validate_plan_steps_evidence_contract(step_list)
    burdens = [
        step.evidence_burden
        for step in step_list
        if step.produces_factual_claims and step.evidence_burden is not None
    ]
    if not burdens:
        return None

    freshness_windows = [
        burden.freshness_window_days
        for burden in burdens
        if burden.freshness_window_days is not None
    ]
    return EvidenceBurden(
        min_independent_sources=max(
            burden.min_independent_sources for burden in burdens
        ),
        require_retrieved_content=any(
            burden.require_retrieved_content for burden in burdens
        ),
        freshness_window_days=(min(freshness_windows) if freshness_windows else None),
    )


# Prevent replanning from silently downgrading an already-governed proof obligation.
def validate_replacement_evidence_obligation(
    *,
    replaced_steps: Iterable[PlanStep],
    replacement_steps: Iterable[PlanStep],
) -> None:
    required = derive_evidence_obligation_floor(replaced_steps)
    if required is None:
        validate_plan_steps_evidence_contract(replacement_steps)
        return

    proposed = derive_evidence_obligation_floor(replacement_steps)
    if proposed is None:
        raise ValueError(
            "Replacement trajectory removed the remaining factual evidence obligation."
        )

    weakened_dimensions: list[str] = []
    if proposed.min_independent_sources < required.min_independent_sources:
        weakened_dimensions.append("min_independent_sources")
    if required.require_retrieved_content and not proposed.require_retrieved_content:
        weakened_dimensions.append("require_retrieved_content")
    if required.freshness_window_days is not None and (
        proposed.freshness_window_days is None
        or proposed.freshness_window_days > required.freshness_window_days
    ):
        weakened_dimensions.append("freshness_window_days")
    if weakened_dimensions:
        raise ValueError(
            "Replacement trajectory weakened the remaining evidence obligation: "
            + ", ".join(weakened_dimensions)
            + "."
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
            "produces_governed_external_effect_proposal": (
                step.produces_governed_external_effect_proposal
            ),
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
    "derive_evidence_obligation_floor",
    "semantic_plan_signature",
    "validate_external_effect_plan_contract",
    "validate_plan_step_evidence_contract",
    "validate_plan_steps_evidence_contract",
    "validate_replacement_evidence_obligation",
]
