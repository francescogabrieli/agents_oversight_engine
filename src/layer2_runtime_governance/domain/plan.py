from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from layer1_intake.models import TaskSpec

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
    insufficiency_declaration_permitted: bool = False

    # Keep the planner-owned burden usable as a deterministic governance contract.
    def __post_init__(self) -> None:
        if not isinstance(self.min_independent_sources, int) or isinstance(
            self.min_independent_sources, bool
        ) or self.min_independent_sources < 1:
            raise ValueError("EvidenceBurden.min_independent_sources must be at least 1.")
        if not isinstance(self.require_retrieved_content, bool):
            raise ValueError("EvidenceBurden.require_retrieved_content must be boolean.")
        if self.freshness_window_days is not None and self.freshness_window_days < 1:
            raise ValueError("EvidenceBurden.freshness_window_days must be at least 1 when set.")
        if not isinstance(self.insufficiency_declaration_permitted, bool):
            raise ValueError(
                "EvidenceBurden.insufficiency_declaration_permitted must be boolean."
            )


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
        insufficiency_declaration_permitted=any(
            burden.insufficiency_declaration_permitted for burden in burdens
        ),
    )


_MIN_SOURCE_PATTERNS = (
    re.compile(
        r"\b(?:at\s+least|minimum(?:\s+of)?|no\s+fewer\s+than)\s+"
        r"(?P<count>\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
        r"(?:(?:independent|valid|reliable|relevant|official|authoritative)\s+)*"
        r"sources?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:almeno|minimo|non\s+meno\s+di)\s+"
        r"(?P<count>\d+|uno|due|tre|quattro|cinque|sei|sette|otto|nove|dieci)\s+"
        r"(?:(?:indipendenti|valide|affidabili|rilevanti|ufficiali|autorevoli)\s+)*"
        r"(?:fonti|fontes?)\b",
        re.IGNORECASE,
    ),
)
_FRESHNESS_PATTERNS = (
    re.compile(
        r"\b(?:within|no\s+older\s+than|in\s+the\s+last|last)\s+"
        r"(?P<days>\d+)\s+days?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:entro|non\s+pi[uù]\s+vecchi[ae]\s+di|ultim[oi])\s+"
        r"(?P<days>\d+)\s+giorni\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bfreshness\s+window\s*[:=]?\s*(?P<days>\d+)\s+days?\b",
        re.IGNORECASE,
    ),
)
_INSUFFICIENCY_PERMISSION_PATTERNS = (
    re.compile(
        r"\b(?:if|when)\b[^.]{0,260}\b(?:fewer|less|insufficient|not\s+enough)\b"
        r"[^.]{0,180}\bsources?\b[^.]{0,180}\b(?:declare|state|explicitly)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:se|qualora)\b[^.]{0,260}\b(?:fonti)\b[^.]{0,160}\b"
        r"(?:meno\s+di|insufficienti|non\s+sufficienti)\b[^.]{0,180}\b"
        r"(?:dichiar|esplicit)\w*\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:declare|state|explicitly\s+declare|dichiar|esplicit)\w*\b"
        r"[^.]{0,180}\b(?:insufficien|insufficient|not\s+enough)\w*\b",
        re.IGNORECASE,
    ),
)

_EXPLICIT_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "uno": 1,
    "due": 2,
    "tre": 3,
    "quattro": 4,
    "cinque": 5,
    "sei": 6,
    "sette": 7,
    "otto": 8,
    "nove": 9,
    "dieci": 10,
}


# Convert only the bounded number vocabulary accepted by the explicit floor patterns.
def _explicit_count(value: str) -> int:
    return int(value) if value.isdigit() else _EXPLICIT_NUMBER_WORDS[value.casefold()]


# Read only explicit task language into the minimum proof contract; no default is inferred.
def derive_task_evidence_floor(task_spec: TaskSpec) -> EvidenceBurden | None:
    task_text = " ".join([*task_spec.constraints, *task_spec.success_criteria])
    source_counts = [
        _explicit_count(match.group("count"))
        for pattern in _MIN_SOURCE_PATTERNS
        for match in pattern.finditer(task_text)
    ]
    freshness_windows = [
        int(match.group("days"))
        for pattern in _FRESHNESS_PATTERNS
        for match in pattern.finditer(task_text)
    ]
    if not source_counts and not freshness_windows:
        return None
    insufficiency_permitted = any(
        pattern.search(task_text) is not None
        for pattern in _INSUFFICIENCY_PERMISSION_PATTERNS
    )
    return EvidenceBurden(
        min_independent_sources=max(source_counts, default=1),
        require_retrieved_content=True,
        freshness_window_days=min(freshness_windows) if freshness_windows else None,
        insufficiency_declaration_permitted=insufficiency_permitted,
    )


# Merge a planner burden with the task minimum without weakening either contract.
def lift_evidence_burden_to_floor(
    burden: EvidenceBurden | None,
    floor: EvidenceBurden | None,
) -> EvidenceBurden | None:
    if burden is None:
        return floor
    if floor is None:
        return burden
    freshness_values = [
        value
        for value in (burden.freshness_window_days, floor.freshness_window_days)
        if value is not None
    ]
    return EvidenceBurden(
        min_independent_sources=max(
            burden.min_independent_sources, floor.min_independent_sources
        ),
        require_retrieved_content=(
            burden.require_retrieved_content or floor.require_retrieved_content
        ),
        freshness_window_days=min(freshness_values) if freshness_values else None,
        insufficiency_declaration_permitted=(
            burden.insufficiency_declaration_permitted
            or floor.insufficiency_declaration_permitted
        ),
    )


# Lift planner burdens to the task-derived minimum while preserving the semantic step marker.
def apply_task_evidence_floor(
    *,
    steps: Iterable[PlanStep],
    task_spec: TaskSpec,
) -> tuple[str, ...]:
    floor = derive_task_evidence_floor(task_spec)
    if floor is None:
        return ()
    corrections: list[str] = []
    for step in steps:
        if not step.produces_factual_claims or step.evidence_burden is None:
            continue
        burden = step.evidence_burden
        # Both operands are non-null here, so the merge always yields a burden.
        corrected = lift_evidence_burden_to_floor(burden, floor) or burden
        if corrected != burden:
            step.evidence_burden = corrected
            corrections.append(
                f"{step.step_id}: evidence burden lifted to task floor"
            )
    return tuple(corrections)


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
    if (
        required.insufficiency_declaration_permitted
        and not proposed.insufficiency_declaration_permitted
    ):
        weakened_dimensions.append("insufficiency_declaration_permitted")
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


# Project the evidence burden while omitting its default compatibility marker.
def project_evidence_burden(burden: EvidenceBurden | None) -> dict[str, object] | None:
    if burden is None:
        return None
    projected: dict[str, object] = {
        "min_independent_sources": burden.min_independent_sources,
        "require_retrieved_content": burden.require_retrieved_content,
        "freshness_window_days": burden.freshness_window_days,
    }
    if burden.insufficiency_declaration_permitted:
        projected["insufficiency_declaration_permitted"] = True
    return projected


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
            "evidence_burden": project_evidence_burden(step.evidence_burden),
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
    "derive_task_evidence_floor",
    "lift_evidence_burden_to_floor",
    "apply_task_evidence_floor",
    "project_evidence_burden",
    "semantic_plan_signature",
    "validate_external_effect_plan_contract",
    "validate_plan_step_evidence_contract",
    "validate_plan_steps_evidence_contract",
    "validate_replacement_evidence_obligation",
]
