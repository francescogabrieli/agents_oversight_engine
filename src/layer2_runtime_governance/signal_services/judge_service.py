from __future__ import annotations

from dataclasses import replace
from typing import Any

from layer2_runtime_governance.domain import TaskState
from layer2_runtime_governance.external_operations import ExternalOperationProposal
from layer2_runtime_governance.tool_contract import ToolExecutionResult
from layer2_runtime_governance.judge import (
    JudgeAssessment,
    JudgeAssessmentStatus,
    JudgeCompletedStepContext,
    JudgeContext,
    JudgeEvidenceContext,
    JudgeExternalRevisionContext,
    JudgeOperationStage,
    JudgePlanStepContext,
    JudgeRecommendedDirective,
    JudgeRuntimeEventContext,
    JudgeScratchpadContext,
    JudgeSeverity,
    JudgeToolArgumentContext,
    JudgeToolExecutionContext,
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
    validation = task_state.actor_result.step_validation if task_state.actor_result else None
    return JudgeContext(
        task_goal=task_state.task_spec.goal,
        task_constraints=tuple(task_state.task_spec.constraints),
        authorized_context=task_state.task_spec.context,
        current_plan_step=JudgePlanStepContext(
            step_id=step.step_id,
            objective=step.objective,
            rationale=step.rationale,
            expected_outcome=step.expected_outcome,
            status=step.status.value,
        ),
        external_operation=external_operation,
        # A communication proposal is inspected before its required human approval.
        operation_stage=(
            JudgeOperationStage.COMMUNICATION_DRAFT_REVIEW
            if external_operation.operation_family.value == "external_communication"
            else JudgeOperationStage.EXECUTION_CANDIDATE
        ),
        # The Judge receives only selected concrete observations, never the whole tool transcript.
        observations=tuple(
            JudgeEvidenceContext(
                evidence_id=item.observation_id,
                locator=item.locator,
                content=item.content,
            )
            for item in task_state.observations
            if item.observation_id in set(validation.evidence_refs if validation else ())
        ),
        selected_evidence_refs=tuple(validation.evidence_refs if validation else ()),
        actor_self_confidence=(validation.actor_self_confidence if validation else None),
        residual_uncertainty=(validation.residual_uncertainty if validation else None),
        destination_facts=(
            f"kind={external_operation.target.kind}",
            f"value={external_operation.target.value}",
            f"class={external_operation.target.destination_class.value}",
        ),
        invocation_reason=invocation_reason,
        task_success_criteria=tuple(task_state.task_spec.success_criteria),
        execution_intent=task_state.execution_intent.value,
        full_plan=tuple(
            JudgePlanStepContext(
                step_id=item.step_id,
                objective=item.objective,
                rationale=item.rationale,
                expected_outcome=item.expected_outcome,
                status=item.status.value,
            )
            for item in task_state.plan_steps
            if item.step_id == step.step_id
        ),
        completed_steps=tuple(
            JudgeCompletedStepContext(
                step_id=item.step_id,
                objective=item.objective,
                result_type=item.result_type.value,
                summary=item.summary,
                output_text=item.output_text,
            )
            for item in task_state.executed_steps
            if item.step_id == step.step_id
        ),
        # Scratchpad, raw tool history, and generic node narration are intentionally excluded.
        scratchpad_entries=(),
        tool_executions=(),
        runtime_events=(),
        prior_external_revisions=_build_prior_external_revisions(task_state=task_state),
    )


# Project one tool result without leaking credentials or provider client state.
def _build_tool_execution_context(
    tool_result: ToolExecutionResult,
) -> JudgeToolExecutionContext:
    request = tool_result.request
    arguments = tuple(
        JudgeToolArgumentContext(name=name, value=str(value))
        for name, value in (
            ("query", request.query),
            ("url", request.url),
            ("text", request.text),
            ("path", request.path),
            ("expression", request.expression),
            ("timezone", request.timezone),
            ("limit", request.limit),
            ("max_results", request.max_results),
            ("extractive_goal", request.extractive_goal),
        )
        if value is not None
    )
    return JudgeToolExecutionContext(
        request_id=request.request_id,
        plan_step_id=request.plan_step_id,
        tool_name=request.tool_name.value,
        reason=request.reason,
        arguments=arguments,
        success=tool_result.success,
        summary=tool_result.summary,
        content=tool_result.content,
        observation_ids=tuple(
            item.observation_id for item in tool_result.observations
        ),
        error_code=_enum_or_string_value(tool_result.error_code),
        error_message=tool_result.error_message,
    )


# Preserve judge context when a legacy checkpoint recovered an enum as its string value.
def _enum_or_string_value(value: object | None) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


# Bind prior proposal digests to any revision feedback already produced by governance.
def _build_prior_external_revisions(
    *,
    task_state: TaskState,
) -> tuple[JudgeExternalRevisionContext, ...]:
    feedback_by_digest = {
        item.invalidated_payload_digest: item
        for item in task_state.governance_action_feedback
    }
    revisions: list[JudgeExternalRevisionContext] = []
    for proposal in task_state.external_operation_history:
        feedback = feedback_by_digest.get(proposal.payload_digest)
        revisions.append(
            JudgeExternalRevisionContext(
                operation_id=proposal.operation_id,
                payload_digest=proposal.payload_digest,
                payload_preview=proposal.payload_preview,
                finding_codes=feedback.finding_codes if feedback is not None else (),
                feedback_summary=feedback.summary if feedback is not None else None,
            )
        )
    return tuple(revisions)


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
