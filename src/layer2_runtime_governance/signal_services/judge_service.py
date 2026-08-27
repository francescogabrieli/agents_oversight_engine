from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from layer2_runtime_governance.domain import (
    TaskState,
    build_evidence_selection_digest,
    build_evidence_snapshot_manifest,
    build_observation_lineage,
    digest_json,
)
from layer3_tool_bus_external_capabilities.external_operations import (
    CommunicationOperationPayload,
    ExternalOperationProposal,
)
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
    JudgeSeverity,
    JudgeStageContract,
    JudgeVerdict,
    build_judge_evidence_bundle,
)


# Project a durable source timestamp only when checkpoint restoration preserved a datetime.
def _source_timestamp_text(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


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
    selected_refs = tuple(validation.evidence_refs if validation else ())
    selected_ref_set = set(selected_refs)
    observation_lineage = build_observation_lineage(task_state=task_state)
    snapshot_manifest = build_evidence_snapshot_manifest(
        task_state=task_state,
        observation_lineage=observation_lineage,
    )
    evidence_snapshot_digest = digest_json(snapshot_manifest)
    evidence_selection_digest = build_evidence_selection_digest(
        selected_refs=selected_refs,
        evidence_snapshot_digest=evidence_snapshot_digest,
        external_operation_digest=external_operation.payload_digest,
    )
    context = JudgeContext(
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
        stage_contract=_build_judge_stage_contract(external_operation=external_operation),
        # The complete canonical observation universe is epistemically available to the judge.
        observations=tuple(
            JudgeEvidenceContext(
                evidence_id=item.observation_id,
                locator=item.locator,
                content=item.content,
                source_timestamp=_source_timestamp_text(
                    getattr(item, "source_timestamp", None)
                ),
                plan_step_id=(
                    observation_lineage[item.observation_id].plan_step_id
                    if item.observation_id in observation_lineage
                    else None
                ),
                selected_by_actor=item.observation_id in selected_ref_set,
                content_truncated=bool(
                    item.observation_id in observation_lineage
                    and (
                        observation_lineage[item.observation_id].content_truncated
                        or observation_lineage[item.observation_id].extractability_limited
                    )
                ),
                incomplete_marker=(
                    "content_truncated"
                    if item.observation_id in observation_lineage
                    and (
                        observation_lineage[item.observation_id].content_truncated
                        or observation_lineage[item.observation_id].extractability_limited
                    )
                    else (
                        "lineage_unresolved"
                        if item.observation_id not in observation_lineage
                        else None
                    )
                ),
            )
            for item in task_state.observations
        ),
        selected_evidence_refs=selected_refs,
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
        ),
        # Scratchpad, raw tool history, and generic node narration are intentionally excluded.
        scratchpad_entries=(),
        tool_executions=(),
        runtime_events=(),
        prior_external_revisions=_build_prior_external_revisions(
            task_state=task_state, operation_id=external_operation.operation_id
        ),
        external_communication_body=(
            external_operation.payload.body
            if isinstance(external_operation.payload, CommunicationOperationPayload)
            else None
        ),
        # Coverage and per-observation extractability are separate contracts.
        # Every canonical observation is represented even when one item reports
        # bounded or incomplete extracted content.
        evidence_base_complete=True,
        observation_count=len(task_state.observations),
        included_observation_count=len(task_state.observations),
        omitted_observation_ids=(),
        evidence_snapshot_digest=evidence_snapshot_digest,
        evidence_selection_digest=evidence_selection_digest,
        serialization_version="judge_context.v6",
    )
    return replace(
        context,
        evidence_bundle=build_judge_evidence_bundle(context=context),
    )


# Make lifecycle scope a local typed fact in the same payload as the proposal.
def _build_judge_stage_contract(
    *,
    external_operation: ExternalOperationProposal,
) -> JudgeStageContract:
    # The external effect is always pending at evaluation time in both stages.
    if isinstance(external_operation.payload, CommunicationOperationPayload):
        return JudgeStageContract(
            stage=JudgeOperationStage.COMMUNICATION_DRAFT_REVIEW,
            side_effect_occurred=False,
            completeness_definition="recipient+subject+body",
            out_of_scope_checks=("proof_of_send", "finalization_markers"),
        )
    return JudgeStageContract(
        stage=JudgeOperationStage.EXECUTION_CANDIDATE,
        side_effect_occurred=False,
        completeness_definition="complete_typed_payload",
        out_of_scope_checks=("proof_of_execution", "finalization_markers"),
    )


# Bind prior proposal digests to any revision feedback already produced by governance.
def _build_prior_external_revisions(
    *,
    task_state: TaskState,
    operation_id: str,
) -> tuple[JudgeExternalRevisionContext, ...]:
    feedback_by_digest = {
        item.invalidated_payload_digest: item
        for item in task_state.governance_action_feedback
    }
    revisions: list[JudgeExternalRevisionContext] = []
    for proposal in task_state.external_operation_history:
        if proposal.operation_id != operation_id:
            continue
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
    expected_evidence_snapshot_digest: str | None,
    expected_evidence_selection_digest: str | None,
    thresholds_version: str,
) -> dict[str, Any]:
    assessment = judge_assessment
    operation_digest_matches = bool(
        assessment
        and expected_operation_digest
        and assessment.evaluated_operation_digest == expected_operation_digest
    )
    evidence_snapshot_matches = bool(
        assessment
        and expected_evidence_snapshot_digest
        and assessment.evaluated_evidence_snapshot_digest
        == expected_evidence_snapshot_digest
    )
    evidence_selection_matches = bool(
        assessment
        and expected_evidence_selection_digest
        and assessment.evaluated_evidence_selection_digest
        == expected_evidence_selection_digest
    )
    digest_matches = bool(
        operation_digest_matches
        and evidence_snapshot_matches
        and evidence_selection_matches
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
                "task_requirement": (
                    {
                        "kind": finding.task_requirement.kind.value,
                        "index": finding.task_requirement.index,
                        "quote": finding.task_requirement.quote,
                    }
                    if finding.task_requirement is not None
                    else None
                ),
                "payload_field": (
                    finding.payload_field.value
                    if finding.payload_field is not None
                    else None
                ),
                "payload_excerpt": finding.payload_excerpt,
            }
            for finding in (assessment.findings if assessment else ())
        ],
        "claim_assessments": [
            {
                "claim_summary": item.claim_summary,
                "status": item.status.value,
                "selected_evidence_refs": list(item.selected_evidence_refs),
                "supporting_evidence_refs": list(item.supporting_evidence_refs),
                "contradicting_evidence_refs": list(item.contradicting_evidence_refs),
                "explanation": item.explanation,
            }
            for item in (assessment.claim_assessments if assessment else ())
        ],
        "summary": assessment.summary if assessment else "Judge was not invoked.",
        # Keep the model rationale auditable in the exported trace, not only in checkpoints.
        "rationale": assessment.rationale if assessment else None,
        "evaluated_operation_digest": (
            assessment.evaluated_operation_digest if assessment else None
        ),
        "expected_operation_digest": expected_operation_digest,
        "evaluated_evidence_snapshot_digest": (
            assessment.evaluated_evidence_snapshot_digest if assessment else None
        ),
        "expected_evidence_snapshot_digest": expected_evidence_snapshot_digest,
        "evidence_snapshot_matches": evidence_snapshot_matches,
        "evaluated_evidence_selection_digest": (
            assessment.evaluated_evidence_selection_digest if assessment else None
        ),
        "expected_evidence_selection_digest": expected_evidence_selection_digest,
        "evidence_selection_matches": evidence_selection_matches,
        "operation_digest_matches": operation_digest_matches,
        "digest_matches": digest_matches,
        "prompt_version": assessment.prompt_version if assessment else None,
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
    expected_evidence_snapshot_digest: str,
    expected_evidence_selection_digest: str,
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
            evaluated_evidence_snapshot_digest=None,
            evaluated_evidence_selection_digest=None,
        )
    if (
        judge_assessment.evaluated_operation_digest != expected_operation_digest
        or judge_assessment.evaluated_evidence_snapshot_digest
        != expected_evidence_snapshot_digest
        or judge_assessment.evaluated_evidence_selection_digest
        != expected_evidence_selection_digest
    ):
        return replace(
            judge_assessment,
            recommendation=JudgeRecommendedDirective.WAIT_HUMAN_REVIEW,
            status=JudgeAssessmentStatus.INVALID,
            verdict=JudgeVerdict.UNCERTAIN,
            severity=JudgeSeverity.HIGH,
            flags=tuple(dict.fromkeys((*judge_assessment.flags, "judge_digest_mismatch"))),
            summary=(
                "Semantic judge assessment is invalid for the current payload "
                "and evidence snapshot binding."
            ),
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
