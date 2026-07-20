"""Pure completeness assessment for canonical Layer 1 task specifications."""

from __future__ import annotations

from .models import (
    DomainType,
    ParseStatus,
    SensitivityLevel,
    TaskCompletenessAssessment,
    TaskSpec,
)


# Assess semantic completeness without mutating the canonical TaskSpec.
def assess_task_completeness(task_spec: TaskSpec) -> TaskCompletenessAssessment:
    warnings: list[str] = []
    missing_information: list[str] = []

    if not task_spec.context:
        warnings.append("Task context is empty.")
    if not task_spec.success_criteria:
        warnings.append("No explicit success criteria were extracted.")
        missing_information.append("success_criteria")
    if task_spec.domain_type == DomainType.OTHER:
        warnings.append("Domain taxonomy fallback OTHER was used.")
    if (
        task_spec.sensitivity in {SensitivityLevel.HIGH, SensitivityLevel.CRITICAL}
        and not task_spec.constraints
    ):
        warnings.append("High-sensitivity task without explicit constraints.")
        missing_information.append("constraints")

    return TaskCompletenessAssessment(
        parse_status=_resolve_parse_status(
            task_spec=task_spec,
            missing_information=missing_information,
        ),
        warnings=sorted(set(warnings)),
        missing_information=sorted(set(missing_information)),
    )


# Resolve the status from the same missing-information facts returned to callers.
def _resolve_parse_status(
    *, task_spec: TaskSpec, missing_information: list[str]
) -> ParseStatus:
    if not missing_information:
        return ParseStatus.COMPLETE
    if task_spec.sensitivity == SensitivityLevel.CRITICAL:
        return ParseStatus.NEEDS_CLARIFICATION
    if "constraints" in missing_information and task_spec.sensitivity == SensitivityLevel.HIGH:
        return ParseStatus.NEEDS_CLARIFICATION
    if len(missing_information) >= 2:
        return ParseStatus.NEEDS_CLARIFICATION
    return ParseStatus.PARTIAL


__all__ = ["assess_task_completeness"]
