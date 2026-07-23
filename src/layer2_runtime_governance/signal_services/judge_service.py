from __future__ import annotations

from dataclasses import replace
from typing import Any

from layer2_runtime_governance.domain.evidence_policy import (
    available_evidence_support_level,
    required_evidence_support_level,
)
from layer2_runtime_governance.domain import TaskState
from layer2_runtime_governance.external_operations import ExternalOperationProposal
from layer2_runtime_governance.judge import (
    JudgeAssessment,
    JudgeAssessmentStatus,
    JudgeContext,
    JudgeEvidenceContext,
    JudgePlanStepContext,
    JudgeRecommendedDirective,
    JudgeSeverity,
    JudgeVerdict,
)


# Build the exact semantic context for the actor step identified by actor_result.
def build_judge_context(
    *,
    task_state: TaskState,
    external_operation: ExternalOperationProposal,
    invocation_reason: str,
) -> JudgeContext:
    step = next(
        (
            candidate
            for candidate in task_state.plan_steps
            if candidate.step_id == external_operation.plan_step_id
        ),
        None,
    )
    if step is None:
        raise ValueError("Cannot build JudgeContext without the evaluated actor plan step.")
    return JudgeContext(
        task_goal=task_state.task_spec.goal,
        task_constraints=tuple(task_state.task_spec.constraints),
        authorized_context=task_state.task_spec.context,
        current_plan_step=JudgePlanStepContext(
            step_id=step.step_id,
            objective=step.objective,
            rationale=step.rationale,
            expected_outcome=step.expected_outcome,
        ),
        external_operation=external_operation,
        observed_evidence=tuple(
            JudgeEvidenceContext(
                evidence_id=item.evidence_id,
                title=item.title,
                locator=item.locator,
                excerpt=item.excerpt,
                support_level=item.support_level,
            )
            for item in task_state.observed_evidence
            if item.plan_step_id == step.step_id or item.plan_step_id == "intake"
        ),
        available_evidence_support_level=available_evidence_support_level(
            task_state=task_state
        ),
        required_evidence_support_level=required_evidence_support_level(
            task_state=task_state
        ),
        destination_facts=(
            f"kind={external_operation.target.kind}",
            f"value={external_operation.target.value}",
            f"class={external_operation.target.destination_class.value}",
        ),
        invocation_reason=invocation_reason,
    )


# Convert the semantic assessment into OPA facts without mapping a runtime directive.
def build_judge_signal(
    *,
    judge_assessment: JudgeAssessment | None,
    required: bool,
    expected_operation_digest: str | None,
    thresholds_version: str,
) -> dict[str, Any]:
    assessment = judge_assessment
    digest_matches = bool(
        assessment
        and expected_operation_digest
        and assessment.evaluated_operation_digest == expected_operation_digest
    )
    usable = bool(
        assessment
        and assessment.invoked
        and assessment.status == JudgeAssessmentStatus.ACTIVE
        and digest_matches
    )
    unavailable = required and not usable
    status = (
        assessment.status.value
        if assessment is not None
        else (
            JudgeAssessmentStatus.UNAVAILABLE.value
            if required
            else JudgeAssessmentStatus.NOT_APPLICABLE.value
        )
    )
    raw_facts = {
        "invoked": bool(assessment and assessment.invoked),
        "required": required,
        "status": status,
        "pillar_unavailable": unavailable,
        "verdict": assessment.verdict.value if assessment and assessment.verdict else "uncertain",
        "severity": assessment.severity.value if assessment else "high",
        "confidence": float(assessment.confidence if assessment else 0.0),
        "findings": [
            {
                "category": finding.category.value,
                "code": finding.code,
                "severity": finding.severity.value,
                "summary": finding.summary,
                "evidence_refs": list(finding.evidence_refs),
            }
            for finding in (assessment.findings if assessment else ())
        ],
        "summary": assessment.summary if assessment else "Judge was not invoked.",
        "evaluated_operation_digest": (
            assessment.evaluated_operation_digest if assessment else None
        ),
        "expected_operation_digest": expected_operation_digest,
        "digest_matches": digest_matches,
        "prompt_version": assessment.prompt_version if assessment else None,
        "model_metadata": (
            {
                "provider": assessment.model_metadata.provider,
                "model": assessment.model_metadata.model,
                "judge_name": assessment.model_metadata.judge_name,
            }
            if assessment and assessment.model_metadata
            else None
        ),
    }
    return {
        "raw_facts": raw_facts,
        "diagnostic_score": None,
        "diagnostic_level": status,
        "score_components": {
            "confidence": raw_facts["confidence"],
            "severity": raw_facts["severity"],
            "digest_matches": digest_matches,
        },
        "reason_facts": [
            finding["code"] for finding in raw_facts["findings"]
        ],
        "summary": raw_facts["summary"],
        "score_formula_version": "external_operation_semantic_judge_v2",
        "thresholds_version": thresholds_version,
    }


# Validate invocation and digest integrity before the assessment enters TaskState.
def validate_required_judge_assessment(
    *,
    judge_assessment: JudgeAssessment,
    expected_operation_digest: str,
) -> JudgeAssessment:
    if not judge_assessment.invoked:
        return replace(
            judge_assessment,
            invoked=False,
            recommendation=JudgeRecommendedDirective.WAIT_HUMAN_REVIEW,
            status=JudgeAssessmentStatus.UNAVAILABLE,
            verdict=JudgeVerdict.UNCERTAIN,
            severity=JudgeSeverity.HIGH,
            flags=tuple(dict.fromkeys((*judge_assessment.flags, "judge_unavailable"))),
            evaluated_operation_digest=None,
        )
    if judge_assessment.evaluated_operation_digest != expected_operation_digest:
        return replace(
            judge_assessment,
            recommendation=JudgeRecommendedDirective.WAIT_HUMAN_REVIEW,
            status=JudgeAssessmentStatus.INVALID,
            verdict=JudgeVerdict.UNCERTAIN,
            severity=JudgeSeverity.HIGH,
            flags=tuple(dict.fromkeys((*judge_assessment.flags, "judge_digest_mismatch"))),
            summary="Semantic judge assessment is invalid for the current payload digest.",
        )
    if judge_assessment.status != JudgeAssessmentStatus.ACTIVE:
        return replace(
            judge_assessment,
            recommendation=JudgeRecommendedDirective.WAIT_HUMAN_REVIEW,
            verdict=JudgeVerdict.UNCERTAIN,
        )
    # A safe verdict cannot coexist with severe semantic findings.
    severe_finding = any(
        finding.severity in {JudgeSeverity.HIGH, JudgeSeverity.CRITICAL}
        for finding in judge_assessment.findings
    )
    if judge_assessment.verdict == JudgeVerdict.SAFE and (
        judge_assessment.severity in {JudgeSeverity.HIGH, JudgeSeverity.CRITICAL}
        or severe_finding
    ):
        return replace(
            judge_assessment,
            recommendation=JudgeRecommendedDirective.WAIT_HUMAN_REVIEW,
            status=JudgeAssessmentStatus.INVALID,
            verdict=JudgeVerdict.UNCERTAIN,
            severity=JudgeSeverity.HIGH,
            flags=tuple(
                dict.fromkeys(
                    (*judge_assessment.flags, "judge_inconsistent_safe_verdict")
                )
            ),
            summary="Semantic judge assessment contains an inconsistent safe verdict.",
        )
    return judge_assessment


__all__ = [
    "build_judge_context",
    "build_judge_signal",
    "validate_required_judge_assessment",
]
