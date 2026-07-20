from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from ..tool_contract import EvidenceSupportLevel

if TYPE_CHECKING:
    from .task_state import TaskState


class EvidenceRequirementSource(str, Enum):
    STEP_VALIDATION = "step_validation"
    PLAN_STEP_EVIDENCE_REQUIREMENT = "plan_step_evidence_requirement"
    PLAN_STEP_CLAIM_PRECISION_CEILING = "plan_step_claim_precision_ceiling"
    PLAN_STEP_KIND = "plan_step_kind"
    HEURISTIC_STEP = "heuristic_step"
    HEURISTIC_OUTPUT = "heuristic_output"


@dataclass(frozen=True)
class EvidenceRequirementResolution:
    required_level: EvidenceSupportLevel
    source: EvidenceRequirementSource


# Rank typed evidence levels for deterministic threshold comparisons.
def evidence_support_level_rank(level: EvidenceSupportLevel) -> int:
    ordering = {
        EvidenceSupportLevel.LOCAL_ONLY: 0,
        EvidenceSupportLevel.SOURCE_DISCOVERY: 1,
        EvidenceSupportLevel.HIGH_LEVEL_SUMMARY: 2,
        EvidenceSupportLevel.DETAILED_EXTRACTED: 3,
        EvidenceSupportLevel.PRIMARY_LEGAL: 4,
    }
    return ordering[level]

# Infer the minimum evidence support implied by semantic step wording.
def required_evidence_support_level_from_scope_text(*, step_text: str) -> EvidenceSupportLevel:
    if _step_is_source_discovery_only(step_text):
        return EvidenceSupportLevel.SOURCE_DISCOVERY

    if _step_allows_conservative_high_level_summary(step_text):
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

    if any(
        marker in step_text
        for marker in (
            "deadline",
            "deadlines",
            "threshold",
            "thresholds",
            "turnover",
            "number of users",
            "article ",
            "full legal text",
        )
    ):
        return EvidenceSupportLevel.PRIMARY_LEGAL

    if any(
        marker in step_text
        for marker in (
            "official source",
            "official sources",
            "official pages",
            "official website",
            "official portal",
            "legislation page",
            "portal",
            "short summary",
            "summary",
            "overview",
            "high-level",
            "concise",
            "core platform services",
            "do's and don'ts",
            "objectives",
            "legal instrument",
            "citation of the legal instrument",
        )
    ):
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

    if any(
        marker in step_text
        for marker in (
            "specific obligation",
            "specific obligations",
            "obligations-by-number",
            "obligation-by-obligation",
            "specific prohibition",
            "specific prohibitions",
            "specific requirements",
            "clause text",
            "citation-ready mapping",
            "claim mapping",
            "criteria",
            "enforcement mechanism",
            "what it requires",
            "who it applies to",
            "regulatory mechanisms",
            "main obligations",
            "entry into force",
            "timeline",
            "applicability details",
        )
    ):
        return EvidenceSupportLevel.DETAILED_EXTRACTED

    if any(
        marker in step_text
        for marker in ("purpose", "scope", "brief summary")
    ):
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

    return EvidenceSupportLevel.LOCAL_ONLY

# Map typed plan-step kinds to conservative evidence thresholds.
def required_evidence_support_level_from_step_kind(*, step_kind) -> EvidenceSupportLevel:
    step_kind_value = getattr(step_kind, "value", step_kind)
    if step_kind_value == "local_transformation":
        return EvidenceSupportLevel.LOCAL_ONLY
    if step_kind_value == "source_discovery":
        return EvidenceSupportLevel.SOURCE_DISCOVERY
    if step_kind_value == "high_level_summary":
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY
    if step_kind_value == "detailed_extraction":
        return EvidenceSupportLevel.DETAILED_EXTRACTED
    if step_kind_value == "compliance_or_legal":
        return EvidenceSupportLevel.PRIMARY_LEGAL
    if step_kind_value == "final_synthesis":
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY
    return EvidenceSupportLevel.LOCAL_ONLY

# Infer the support required by the factual precision of an actor output.
def required_evidence_support_level_from_output_text(*, text: str) -> EvidenceSupportLevel:
    lowered_text = _normalize_output_text_for_precision_checks(text).lower()

    if _output_text_is_source_shortlist_only(text.lower()):
        return EvidenceSupportLevel.SOURCE_DISCOVERY

    if _output_text_is_conservative_high_level_summary(lowered_text):
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

    precision_markers = _extract_precision_claim_markers(text=text)
    if precision_markers:
        if any(
            marker in lowered_text
            for marker in (
                "threshold",
                "thresholds",
                "turnover",
                "number of users",
                "specific size",
                "article ",
                "deadline",
                "deadlines",
            )
        ):
            return EvidenceSupportLevel.PRIMARY_LEGAL

        if any(
            marker in lowered_text
            for marker in (
                "entered into force",
                "comes into force",
                "coming into force",
                "effective date",
                "effective from",
                "applies from",
                "applies on",
                "entry into force",
                "adopted on",
                "published on",
                "timeline",
                "implementation date",
                "applicability details",
            )
        ):
            return EvidenceSupportLevel.DETAILED_EXTRACTED

        if any(
            marker in precision_markers
            for marker in (
                "percentages",
                "large numeric thresholds",
                "monetary thresholds",
            )
        ):
            return EvidenceSupportLevel.PRIMARY_LEGAL

        return EvidenceSupportLevel.DETAILED_EXTRACTED

    if any(
        marker in lowered_text
        for marker in (
            "threshold",
            "thresholds",
            "turnover",
            "number of users",
            "specific size",
            "article ",
        )
    ):
        return EvidenceSupportLevel.PRIMARY_LEGAL

    if any(
        marker in lowered_text
        for marker in (
            "specific obligation",
            "specific obligations",
            "prohibit",
            "prohibiting",
            "must",
            "shall",
            "cannot",
            "criteria",
            "interoperability",
            "data portability",
            "ranking",
            "non-discriminatory access",
        )
    ):
        return EvidenceSupportLevel.DETAILED_EXTRACTED

    if any(
        marker in lowered_text
        for marker in (
            "summary",
            "overview",
            "aims to",
            "applies to",
            "official sources",
        )
    ):
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

    return EvidenceSupportLevel.LOCAL_ONLY


# Resolve typed step metadata before falling back to conservative semantic wording.
def resolve_required_evidence_support_context(
    *,
    task_state: "TaskState",
    current_step: Any | None = None,
) -> EvidenceRequirementResolution:
    step = current_step or _evaluated_plan_step(task_state=task_state)
    if step is None:
        return EvidenceRequirementResolution(
            EvidenceSupportLevel.LOCAL_ONLY,
            EvidenceRequirementSource.HEURISTIC_STEP,
        )
    if step.evidence_requirement is not None:
        return EvidenceRequirementResolution(
            _coerce_support_level(step.evidence_requirement),
            EvidenceRequirementSource.PLAN_STEP_EVIDENCE_REQUIREMENT,
        )
    if step.claim_precision_ceiling is not None:
        return EvidenceRequirementResolution(
            _coerce_support_level(step.claim_precision_ceiling),
            EvidenceRequirementSource.PLAN_STEP_CLAIM_PRECISION_CEILING,
        )
    if step.step_kind is not None:
        return EvidenceRequirementResolution(
            required_evidence_support_level_from_step_kind(step_kind=step.step_kind),
            EvidenceRequirementSource.PLAN_STEP_KIND,
        )
    scope = " ".join((step.objective, step.rationale, step.expected_outcome)).lower()
    return EvidenceRequirementResolution(
        required_evidence_support_level_from_scope_text(step_text=scope),
        EvidenceRequirementSource.HEURISTIC_STEP,
    )


# Return evidence items with substantive excerpts for raw OPA facts.
def meaningful_observed_evidence(*, task_state: "TaskState") -> tuple[Any, ...]:
    return tuple(
        item
        for item in task_state.observed_evidence
        if item.excerpt.strip()
        and "content has not been extracted" not in item.excerpt.lower()
        and "extracted readable text was empty" not in item.excerpt.lower()
    )


# Return the strongest support level available in canonical observed evidence.
def available_evidence_support_level(*, task_state: "TaskState") -> EvidenceSupportLevel:
    evidence = meaningful_observed_evidence(task_state=task_state)
    if not evidence:
        return EvidenceSupportLevel.LOCAL_ONLY
    return max((item.support_level for item in evidence), key=evidence_support_level_rank)


# Return the current typed evidence requirement.
def required_evidence_support_level(*, task_state: "TaskState") -> EvidenceSupportLevel:
    return resolve_required_evidence_support_context(task_state=task_state).required_level


# Infer claim precision from the latest actor output without changing that output.
def output_claim_support_level(*, task_state: "TaskState") -> EvidenceSupportLevel:
    output_text = task_state.actor_result.output_text if task_state.actor_result is not None else None
    return required_evidence_support_level_from_output_text(text=output_text or "")


# Compare emitted precision with both step need and available evidence.
def precision_mismatch_detected(*, task_state: "TaskState") -> bool:
    available = available_evidence_support_level(task_state=task_state)
    required = effective_support_check_level(
        required_level=required_evidence_support_level(task_state=task_state),
        output_claim_level=output_claim_support_level(task_state=task_state),
    )
    return evidence_support_level_rank(available) < evidence_support_level_rank(required)


# Use the stricter of semantic requirement and emitted claim precision.
def effective_support_check_level(
    *,
    required_level: EvidenceSupportLevel,
    output_claim_level: EvidenceSupportLevel,
) -> EvidenceSupportLevel:
    return max((required_level, output_claim_level), key=evidence_support_level_rank)


# Return the last validation associated with the currently evaluated actor step.
def latest_relevant_validation(*, task_state: "TaskState"):
    step_id = task_state.actor_result.plan_step_id if task_state.actor_result is not None else None
    for validation in reversed(task_state.step_validations):
        if step_id is None or validation.plan_step_id == step_id:
            return validation
    return None


# Return the latest tool result associated with the evaluated actor step.
def latest_tool_result_for_evaluated_step(*, task_state: "TaskState"):
    step_id = task_state.actor_result.plan_step_id if task_state.actor_result is not None else None
    candidates = [*task_state.tool_results_history]
    if task_state.last_tool_result is not None:
        candidates.append(task_state.last_tool_result)
    for result in reversed(candidates):
        if step_id is None or result.request.plan_step_id == step_id:
            return result
    return None


# Resolve the semantic step associated with the latest actor result.
def _evaluated_plan_step(*, task_state: "TaskState"):
    step_id = task_state.actor_result.plan_step_id if task_state.actor_result is not None else None
    if step_id is not None:
        for step in task_state.plan_steps:
            if step.step_id == step_id:
                return step
    return task_state.current_plan_step


# Accept legacy string values while keeping public callers typed.
def _coerce_support_level(value: Any) -> EvidenceSupportLevel:
    return value if isinstance(value, EvidenceSupportLevel) else EvidenceSupportLevel(value)

# Detect step wording that explicitly permits a conservative high-level answer.
def _step_allows_conservative_high_level_summary(scope_text: str) -> bool:
    summary_markers = (
        "short summary",
        "brief summary",
        "few sentences",
        "high level",
        "high-level",
        "purpose/scope-level",
        "conservative",
        "broad categories",
        "obligation themes",
        "does not require",
        "do not require",
    )
    return any(marker in scope_text for marker in summary_markers)

# Detect output that remains broad enough for high-level extracted evidence.
def _output_text_is_conservative_high_level_summary(text: str) -> bool:
    if _extract_precision_claim_markers(text=text):
        return False

    high_level_markers = (
        "brief summary",
        "short summary",
        "overview",
        "purpose",
        "aims to",
        "intended to",
        "in general",
        "at a high level",
        "core platform services",
        "official sources",
    )
    if not any(marker in text for marker in high_level_markers):
        return False

    granular_markers = (
        "article ",
        "threshold",
        "thresholds",
        "turnover",
        "number of users",
        "specific size",
        "must ",
        "shall ",
        "cannot ",
        "non-discriminatory access",
        "data portability",
        "interoperability",
        "ranking",
    )
    return not any(marker in text for marker in granular_markers)

# Distinguish a source shortlist from factual synthesis over source contents.
def _output_text_is_source_shortlist_only(text: str) -> bool:
    lines = [line.strip().lower() for line in text.splitlines() if line.strip()]
    if not lines:
        return False

    if any(
        marker in text
        for marker in (
            "aims to",
            "applies to",
            "purpose",
            "scope",
            "obligation",
            "obligations",
            "requires",
            "requirement",
            "criteria",
            "timeline",
            "entry into force",
        )
    ):
        return False

    source_identity_markers = (
        "official sources",
        "shortlist of official",
        "source shortlist",
        "summary page",
        "legislation page",
        "official portal",
    )
    if not any(marker in text for marker in source_identity_markers):
        return False

    return any("http://" in line or "https://" in line for line in lines)

# Detect steps whose objective ends at source discovery.
def _step_is_source_discovery_only(scope_text: str) -> bool:
    discovery_markers = ("identify", "find", "source", "sources", "links", "references", "citations")
    summary_markers = (
        "summary",
        "summar",
        "purpose",
        "scope",
        "obligation",
        "requirement",
        "criteria",
        "timeline",
        "what it requires",
    )
    return any(marker in scope_text for marker in discovery_markers) and not any(
        marker in scope_text for marker in summary_markers
    )

# Normalize punctuation variants before deterministic precision checks.
def _normalize_output_text_for_precision_checks(text: str) -> str:
    normalized = re.sub(r"\[\d+\]", " ", text)
    normalized = re.sub(r"https?://\S+", " ", normalized)
    return " ".join(normalized.split())

# Extract explicit precision markers for audit diagnostics.
def _extract_precision_claim_markers(*, text: str) -> list[str]:
    normalized = _normalize_output_text_for_precision_checks(text)
    markers: list[str] = []

    precision_context_markers = (
        "entered into force",
        "comes into force",
        "coming into force",
        "effective date",
        "effective from",
        "applies from",
        "applies on",
        "entry into force",
        "adopted on",
        "published on",
        "deadline",
        "deadlines",
        "timeline",
        "applicability details",
        "implementation date",
    )
    has_precision_context = any(marker in normalized for marker in precision_context_markers)

    year_matches = sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", normalized)))
    if year_matches and has_precision_context:
        markers.append(f"calendar years ({', '.join(year_matches[:3])})")

    if has_precision_context and re.search(
        r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b",
        normalized,
        flags=re.IGNORECASE,
    ):
        markers.append("named calendar dates")

    if re.search(r"\b\d+(?:\.\d+)?\s*%", normalized):
        markers.append("percentages")

    if re.search(r"\b\d+(?:[.,]\d+)?\s*(?:million|billion)\b", normalized, flags=re.IGNORECASE):
        markers.append("large numeric thresholds")

    if re.search(r"(?:€|\$|eur\s*)\d", normalized, flags=re.IGNORECASE):
        markers.append("monetary thresholds")

    return markers

__all__ = [
    "evidence_support_level_rank",
    "required_evidence_support_level_from_scope_text",
    "required_evidence_support_level_from_step_kind",
    "required_evidence_support_level_from_output_text",
    "EvidenceRequirementResolution",
    "EvidenceRequirementSource",
    "available_evidence_support_level",
    "effective_support_check_level",
    "latest_relevant_validation",
    "latest_tool_result_for_evaluated_step",
    "meaningful_observed_evidence",
    "output_claim_support_level",
    "precision_mismatch_detected",
    "required_evidence_support_level",
    "resolve_required_evidence_support_context",
]
