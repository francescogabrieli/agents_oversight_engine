from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from json_serialization import to_jsonable as _to_jsonable

from layer4_state_evidence_observability.behavioral import BehavioralAssessment
from ..governance_components import GovernanceDecision, governance_reason_code_values
from layer4_state_evidence_observability.oversight.contracts import DynamicPolicyAssessment, OversightAssessment
from ..domain import (
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewExternalEdit,
    HumanReviewRequest,
    RuntimeState,
    TaskState,
    ExternalCommunicationDraft,
    normalize_task_state_serialization,
)


# DashboardFocus keeps the operator surface aligned with the runtime pause state.
class DashboardFocus(str, Enum):
    LIVE = "live"
    WAITING_HUMAN_REVIEW = "waiting_human_review"
    COMPLETED = "completed"
    ABORTED = "aborted"


# DashboardPlanStep is the minimal semantic step summary the UI needs to render.
@dataclass(frozen=True)
class DashboardPlanStep:
    step_id: str
    objective: str
    rationale: str
    expected_outcome: str
    status: str


# DashboardGovernancePanel exposes the latest policy/control decision.
@dataclass(frozen=True)
class DashboardGovernancePanel:
    governance_input_schema_version: str | None
    directive: str
    policy_decision: str
    requires_human_review: bool
    reason_codes: tuple[str, ...]
    summary: str
    constrains_capability_use: bool
    policy_directive: str
    oversight_recommended_directive: str
    evaluated_actor_result_type: str
    evaluated_tool_name: str | None
    policy_engine_name: str
    policy_bundle_version: str
    policy_config_version: str | None
    static_policy_summary: str | None
    dynamic_policy_summary: str | None
    behavioral_summary: str | None
    judge_summary: str | None
    behavioral_recommended_directive: str
    judge_recommended_directive: str | None
    judge_invoked: bool
    behavioral_similarity_score: float | None
    behavioral_deviation_score: float | None
    behavioral_novelty_score: float | None
    pillar_requirements: dict[str, bool] | None
    pillar_results: dict[str, Any] | None
    pillar_errors: dict[str, str] | None
    selected_reason: str | None
    rejected_alternatives: dict[str, str] | None
    safety_invariants_checked: tuple[str, ...]


# DashboardOversightPanel exposes the deterministic adaptive oversight result.
@dataclass(frozen=True)
class DashboardOversightPanel:
    evidence_quality_score: float | None
    evidence_quality_level: str
    evidence_quality_reason_codes: tuple[str, ...]
    operational_risk_score: float | None
    operational_risk_level: str
    operational_risk_reason_codes: tuple[str, ...]
    operational_risk_mode: str
    operational_risk_decision_influence: bool
    operational_risk_applicability: str
    operational_risk_consequence_facts: dict[str, Any]
    trajectory_health_score: float | None
    trajectory_health_level: str
    trajectory_health_reason_codes: tuple[str, ...]
    recommended_directive: str
    summary: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class DashboardBehavioralPanel:
    # DashboardBehavioralPanel exposes the behavioural history, scores, and advisory directive.
    history_available: bool
    historical_run_count: int
    feature_snapshot_version: str
    summary: str
    notes: tuple[str, ...]
    matched_run_count: int
    historical_similarity_score: float | None
    behavior_deviation_score: float | None
    novelty_score: float | None
    behavioral_recommended_directive: str
    behavioral_flags: tuple[str, ...]
    matched_run_ids: tuple[str, ...]
    mode: str
    decision_influence: bool
    status: str


# DashboardPillarDiagnostic exposes only review-relevant policy facts to the operator.
@dataclass(frozen=True)
class DashboardPillarDiagnostic:
    pillar_name: str
    required: bool
    mode: str | None
    level: str | None
    summary: str | None
    reason_codes: tuple[str, ...]
    facts: dict[str, Any]


# DashboardHumanReviewPanel captures the review payload and resume path.
@dataclass(frozen=True)
class DashboardHumanReviewPanel:
    pending_request: HumanReviewRequest | None
    last_request: HumanReviewRequest | None
    last_decision: HumanReviewDecision | None
    decision_history: tuple[HumanReviewDecision, ...]
    allowed_decisions: tuple[HumanReviewDecisionType, ...]
    resume_state: str | None
    review_summary: str | None
    external_communication_target_type: str | None
    external_communication_draft: ExternalCommunicationDraft | None
    operation_id: str | None
    operation_payload_digest: str | None
    operation_proposal_summary: str | None
    operation_target: str | None
    judge_findings: tuple[str, ...]
    evidence_diagnostic: DashboardPillarDiagnostic | None
    trajectory_diagnostic: DashboardPillarDiagnostic | None


@dataclass(frozen=True)
class DashboardExternalCommunicationPanel:
    # Expose the governed external communication phase without inventing new UI logic.
    phase: str
    draft: ExternalCommunicationDraft | None
    last_applied_edit: HumanReviewExternalEdit | None
    last_transition_summary: str | None


# DashboardExternalExecutionPanel distinguishes authorization from actual execution.
@dataclass(frozen=True)
class DashboardExternalExecutionPanel:
    execution_intent: str
    capability_id: str | None
    status: str | None
    payload_digest: str | None
    connector_kind: str | None
    connector_id: str | None
    server_id: str | None
    tool_name: str | None
    external_reference: str | None
    summary: str | None
    error_code: str | None
    retryable: bool
    attempt_count: int
    receipt_count: int


# GovernanceHitlDashboardSnapshot is the UI-facing contract derived from TaskState.
@dataclass(frozen=True)
class GovernanceHitlDashboardSnapshot:
    dashboard_version: str
    focus: DashboardFocus
    runtime_state: str
    runtime_thread_id: str | None
    current_plan_step: DashboardPlanStep | None
    governance: DashboardGovernancePanel | None
    oversight: DashboardOversightPanel | None
    behavioral: DashboardBehavioralPanel | None
    human_review: DashboardHumanReviewPanel | None
    reason_codes: tuple[str, ...]
    resume_state: str | None
    terminal_state: str | None
    has_pending_review: bool
    is_terminal: bool
    external_communication: DashboardExternalCommunicationPanel | None
    external_execution: DashboardExternalExecutionPanel


# Keep the prominent HITL panel bounded to facts an operator can act on.
_EVIDENCE_REVIEW_FACTS = (
    "completion_attempt",
    "factual_completion_attempt",
    "coverage_evaluated",
    "burden_satisfied",
    "evidence_gap",
    "evidence_request_ceiling_reached",
    "evidence_request_count",
    "evidence_request_episode_count",
    "evidence_request_episode_id",
    "selected_evidence_ref_count",
    "distinct_independent_sources_count",
    "min_independent_sources_required",
    "independent_sources_satisfied",
    "retrieved_content_satisfied",
    "selected_evidence_refs_valid",
    "selected_evidence_refs_unique",
    "selected_observations_have_content",
    "selected_observations_provenance_resolvable",
    "freshness_evaluated",
    "freshness_satisfied",
    "freshness_missing_observation_ids",
    "stale_evidence_observation_ids",
    "future_source_timestamp_observation_ids",
    "circular_reference_detected",
    "has_unanchored_literals_in_selection",
    "has_unanchored_literals_in_universe",
    "factual_step_missing_evidence_burden",
)

_TRAJECTORY_REVIEW_FACTS = (
    "replan_count",
    "replan_episode_count",
    "max_replans_before_review",
    "replan_ceiling_reached",
    "replan_request_admissible",
    "replan_request_stalled",
    "consecutive_tool_failures",
    "consecutive_tool_failures_review_threshold_reached",
    "failed_tool_call_count",
    "successful_tool_call_count",
    "tool_call_count",
    "action_churn_count",
    "action_churn",
    "external_action_revision_count",
    "action_revision_limit_reached",
    "unchanged_resubmission",
    "repeated_rejected_payload_digest",
    "observable_change_since_last_replan",
    "progress_epoch",
)




# Build the dashboard snapshot from the canonical TaskState.
def build_governance_hitl_dashboard_snapshot(
    task_state: TaskState,
) -> GovernanceHitlDashboardSnapshot:
    task_state = normalize_task_state_serialization(task_state)
    current_state = task_state.current_state
    focus = _resolve_dashboard_focus(task_state=current_state)
    governance_panel = _build_governance_panel(task_state.last_governance_decision)
    oversight_panel = _build_oversight_panel(
        task_state.last_dynamic_policy_assessment or task_state.last_oversight_assessment
    )
    behavioral_panel = _build_behavioral_panel(task_state.last_behavioral_assessment)
    human_review_panel = _build_human_review_panel(task_state=task_state)
    current_plan_step = task_state.current_plan_step

    return GovernanceHitlDashboardSnapshot(
        dashboard_version="governance_hitl_dashboard_v2",
        focus=focus,
        runtime_state=current_state.value,
        runtime_thread_id=task_state.runtime_thread_id,
        current_plan_step=(
            _build_plan_step(current_plan_step) if current_plan_step is not None else None
        ),
        governance=governance_panel,
        oversight=oversight_panel,
        behavioral=behavioral_panel,
        human_review=human_review_panel,
        reason_codes=_resolve_reason_codes(task_state=task_state),
        resume_state=(
            task_state.human_review_resume_state.value
            if task_state.human_review_resume_state is not None
            else _resolve_resume_state(task_state)
        ),
        terminal_state=_resolve_terminal_state(task_state=current_state),
        has_pending_review=task_state.pending_human_review_request is not None,
        is_terminal=current_state in {RuntimeState.COMPLETED, RuntimeState.ABORTED},
        external_communication=_build_external_communication_panel(task_state=task_state),
        external_execution=_build_external_execution_panel(task_state=task_state),
    )


# Convert the dashboard snapshot into JSON-safe Python primitives.
def governance_hitl_dashboard_snapshot_to_dict(
    snapshot: GovernanceHitlDashboardSnapshot,
) -> dict[str, Any]:
    return _to_jsonable(snapshot)


# Convert the dashboard snapshot into stable formatted JSON.
def governance_hitl_dashboard_snapshot_to_json(
    snapshot: GovernanceHitlDashboardSnapshot,
) -> str:
    return json.dumps(governance_hitl_dashboard_snapshot_to_dict(snapshot), indent=2, ensure_ascii=False)


# Build the compact semantic step summary for the dashboard surface.
def _build_plan_step(plan_step) -> DashboardPlanStep:
    return DashboardPlanStep(
        step_id=plan_step.step_id,
        objective=plan_step.objective,
        rationale=plan_step.rationale,
        expected_outcome=plan_step.expected_outcome,
        status=plan_step.status.value,
    )


# Build the latest governance panel if the runtime has reached governance.
def _build_governance_panel(
    governance_decision: GovernanceDecision | None,
) -> DashboardGovernancePanel | None:
    if governance_decision is None:
        return None

    return DashboardGovernancePanel(
        governance_input_schema_version=governance_decision.governance_input_schema_version,
        directive=governance_decision.directive.value,
        policy_decision=governance_decision.policy_decision.value,
        requires_human_review=governance_decision.requires_human_review,
        reason_codes=governance_reason_code_values(governance_decision),
        summary=governance_decision.summary,
        constrains_capability_use=governance_decision.constrains_capability_use,
        policy_directive=governance_decision.policy_directive.value,
        oversight_recommended_directive=governance_decision.oversight_recommended_directive.value,
        evaluated_actor_result_type=governance_decision.evaluated_actor_result_type.value,
        evaluated_tool_name=(
            governance_decision.evaluated_tool_name.value
            if governance_decision.evaluated_tool_name is not None
            else None
        ),
        policy_engine_name=governance_decision.policy_engine_name,
        policy_bundle_version=governance_decision.policy_bundle_version,
        policy_config_version=governance_decision.policy_config_version,
        static_policy_summary=governance_decision.static_policy_summary,
        dynamic_policy_summary=governance_decision.dynamic_policy_summary,
        behavioral_summary=governance_decision.behavioral_summary,
        judge_summary=governance_decision.judge_summary,
        judge_recommended_directive=(
            governance_decision.judge_recommended_directive.value
            if governance_decision.judge_recommended_directive is not None
            else None
        ),
        behavioral_recommended_directive=governance_decision.behavioral_recommended_directive.value,
        judge_invoked=governance_decision.judge_invoked,
        behavioral_similarity_score=governance_decision.behavioral_similarity_score,
        behavioral_deviation_score=governance_decision.behavioral_deviation_score,
        behavioral_novelty_score=governance_decision.behavioral_novelty_score,
        pillar_requirements=governance_decision.pillar_requirements,
        pillar_results=governance_decision.pillar_results,
        pillar_errors=governance_decision.pillar_errors,
        selected_reason=governance_decision.combiner_selected_reason,
        rejected_alternatives=governance_decision.combiner_rejected_alternatives,
        safety_invariants_checked=governance_decision.combiner_safety_invariants_checked,
    )


# Build the external execution panel from canonical intent, proposal, and receipt state.
def _build_external_execution_panel(
    *,
    task_state: TaskState,
) -> DashboardExternalExecutionPanel:
    proposal = task_state.external_operation_proposal
    receipt = task_state.last_external_execution_receipt
    return DashboardExternalExecutionPanel(
        execution_intent=task_state.execution_intent.value,
        capability_id=proposal.capability_id if proposal is not None else None,
        status=receipt.status.value if receipt is not None else None,
        payload_digest=(
            receipt.payload_digest
            if receipt is not None
            else proposal.payload_digest
            if proposal is not None
            else None
        ),
        connector_kind=receipt.connector_kind if receipt is not None else None,
        connector_id=receipt.connector_id if receipt is not None else None,
        server_id=receipt.server_id if receipt is not None else None,
        tool_name=receipt.tool_name if receipt is not None else None,
        external_reference=(
            receipt.external_reference if receipt is not None else None
        ),
        summary=receipt.summary if receipt is not None else None,
        error_code=receipt.error_code.value if receipt is not None else None,
        retryable=receipt.retryable if receipt is not None else False,
        attempt_count=task_state.external_execution_attempt_count,
        receipt_count=len(task_state.external_execution_history),
    )


# Build the latest deterministic oversight panel if one exists.
def _build_oversight_panel(
    oversight_assessment: DynamicPolicyAssessment | OversightAssessment | None,
) -> DashboardOversightPanel | None:
    if oversight_assessment is None:
        return None

    return DashboardOversightPanel(
        evidence_quality_score=oversight_assessment.evidence_quality.score,
        evidence_quality_level=oversight_assessment.evidence_quality.level.value,
        evidence_quality_reason_codes=tuple(
            code.value for code in oversight_assessment.evidence_quality.reason_codes
        ),
        operational_risk_score=oversight_assessment.operational_risk.score,
        operational_risk_level=oversight_assessment.operational_risk.level.value,
        operational_risk_reason_codes=tuple(
            code.value for code in oversight_assessment.operational_risk.reason_codes
        ),
        operational_risk_mode=oversight_assessment.operational_risk.mode,
        operational_risk_decision_influence=(
            oversight_assessment.operational_risk.decision_influence
        ),
        operational_risk_applicability=oversight_assessment.operational_risk.applicability,
        operational_risk_consequence_facts={
            "recoverability": oversight_assessment.operational_risk.recoverability,
            "blast_radius": oversight_assessment.operational_risk.blast_radius,
            "sensitivity": oversight_assessment.operational_risk.sensitivity,
            "exposure": oversight_assessment.operational_risk.exposure,
            "target_criticality": oversight_assessment.operational_risk.target_criticality,
            "impact_level": oversight_assessment.operational_risk.impact_level,
            "dominant_factors": list(
                oversight_assessment.operational_risk.dominant_factors
            ),
            "unknown_facts": list(oversight_assessment.operational_risk.unknown_facts),
            "fact_provenance": list(
                oversight_assessment.operational_risk.fact_provenance
            ),
            "profile_version": oversight_assessment.operational_risk.profile_version,
        },
        trajectory_health_score=oversight_assessment.trajectory_health.score,
        trajectory_health_level=oversight_assessment.trajectory_health.level.value,
        trajectory_health_reason_codes=tuple(
            code.value for code in oversight_assessment.trajectory_health.reason_codes
        ),
        recommended_directive=oversight_assessment.recommended_directive.value,
        summary=oversight_assessment.summary,
        reason_codes=tuple(code.value for code in oversight_assessment.reason_codes),
    )


# Build the latest behavioural panel if a foundation assessment exists.
def _build_behavioral_panel(
    behavioral_assessment: BehavioralAssessment | None,
) -> DashboardBehavioralPanel | None:
    if behavioral_assessment is None:
        return None

    return DashboardBehavioralPanel(
        history_available=behavioral_assessment.history_available,
        historical_run_count=behavioral_assessment.historical_run_count,
        feature_snapshot_version=behavioral_assessment.feature_snapshot_version,
        summary=behavioral_assessment.summary,
        notes=behavioral_assessment.notes,
        matched_run_count=behavioral_assessment.matched_run_count,
        historical_similarity_score=behavioral_assessment.historical_similarity_score,
        behavior_deviation_score=behavioral_assessment.behavior_deviation_score,
        novelty_score=behavioral_assessment.novelty_score,
        behavioral_recommended_directive=behavioral_assessment.behavioral_recommended_directive.value,
        behavioral_flags=behavioral_assessment.behavioral_flags,
        matched_run_ids=behavioral_assessment.matched_run_ids,
        mode=behavioral_assessment.mode,
        decision_influence=behavioral_assessment.decision_influence,
        status=behavioral_assessment.status,
    )


# Build the human review panel from pending and historical review data.
def _build_human_review_panel(*, task_state: TaskState) -> DashboardHumanReviewPanel | None:
    pending_request = task_state.pending_human_review_request
    last_request = task_state.last_human_review_request
    last_decision = task_state.last_human_review_decision

    if (
        pending_request is None
        and last_request is None
        and last_decision is None
        and not task_state.human_review_history
    ):
        return None

    return DashboardHumanReviewPanel(
        pending_request=pending_request,
        last_request=last_request,
        last_decision=last_decision,
        decision_history=tuple(task_state.human_review_history),
        allowed_decisions=(
            pending_request.allowed_decisions if pending_request is not None else tuple()
        ),
        resume_state=(
            pending_request.resume_state.value
            if pending_request is not None
            else (
                task_state.human_review_resume_state.value
                if task_state.human_review_resume_state is not None
                else None
            )
        ),
        review_summary=(
            pending_request.review_summary
            if pending_request is not None
            else _build_review_summary_from_history(
                last_request=last_request,
                last_decision=last_decision,
            )
        ),
        external_communication_target_type=(
            pending_request.external_communication_target_type
            if pending_request is not None
            else None
        ),
        external_communication_draft=(
            pending_request.external_communication_draft
            if pending_request is not None
            else None
        ),
        operation_id=pending_request.operation_id if pending_request is not None else None,
        operation_payload_digest=(
            pending_request.operation_payload_digest
            if pending_request is not None
            else None
        ),
        operation_proposal_summary=(
            pending_request.operation_proposal_summary
            if pending_request is not None
            else None
        ),
        operation_target=(
            pending_request.operation_target if pending_request is not None else None
        ),
        judge_findings=(
            pending_request.judge_findings if pending_request is not None else ()
        ),
        evidence_diagnostic=_build_pillar_diagnostic(
            governance_decision=task_state.last_governance_decision,
            pillar_name="evidence",
            fact_names=_EVIDENCE_REVIEW_FACTS,
        ),
        trajectory_diagnostic=_build_pillar_diagnostic(
            governance_decision=task_state.last_governance_decision,
            pillar_name="trajectory",
            fact_names=_TRAJECTORY_REVIEW_FACTS,
        ),
    )


# Project one OPA pillar result into a compact, typed operator-facing diagnostic.
def _build_pillar_diagnostic(
    *,
    governance_decision: GovernanceDecision | None,
    pillar_name: str,
    fact_names: tuple[str, ...],
) -> DashboardPillarDiagnostic | None:
    if governance_decision is None or governance_decision.pillar_results is None:
        return None

    result = governance_decision.pillar_results.get(pillar_name)
    if not isinstance(result, dict):
        return None

    raw_facts = result.get("raw_facts")
    raw_fact_values = raw_facts if isinstance(raw_facts, dict) else {}
    facts: dict[str, Any] = {}
    for fact_name in fact_names:
        if fact_name in result and result[fact_name] is not None:
            facts[fact_name] = result[fact_name]
        elif fact_name in raw_fact_values and raw_fact_values[fact_name] is not None:
            facts[fact_name] = raw_fact_values[fact_name]

    reason_codes = result.get("reason_codes")
    if not isinstance(reason_codes, list | tuple):
        reason_codes = result.get("reason_facts")
    normalized_reason_codes = (
        tuple(str(code) for code in reason_codes)
        if isinstance(reason_codes, list | tuple)
        else tuple()
    )
    requirements = governance_decision.pillar_requirements or {}
    level = result.get("diagnostic_level", result.get("level"))
    return DashboardPillarDiagnostic(
        pillar_name=pillar_name,
        required=bool(requirements.get(f"require_{pillar_name}", False)),
        mode=str(result["mode"]) if result.get("mode") is not None else None,
        level=str(level) if level is not None else None,
        summary=str(result["summary"]) if result.get("summary") is not None else None,
        reason_codes=normalized_reason_codes,
        facts=facts,
    )


# Build the external communication phase panel when the task uses that branch.
def _build_external_communication_panel(
    *,
    task_state: TaskState,
) -> DashboardExternalCommunicationPanel | None:
    if task_state.external_communication is None:
        return None
    return DashboardExternalCommunicationPanel(
        phase=task_state.external_communication.phase.value,
        draft=task_state.external_communication.draft,
        last_applied_edit=task_state.external_communication.last_applied_edit,
        last_transition_summary=task_state.external_communication.last_transition_summary,
    )


# Resolve the dashboard focus from the current runtime state.
def _resolve_dashboard_focus(*, task_state: RuntimeState) -> DashboardFocus:
    if task_state == RuntimeState.ABORTED:
        return DashboardFocus.ABORTED
    if task_state == RuntimeState.COMPLETED:
        return DashboardFocus.COMPLETED
    if task_state == RuntimeState.WAITING_HUMAN_REVIEW:
        return DashboardFocus.WAITING_HUMAN_REVIEW
    return DashboardFocus.LIVE


# Resolve a stable readout for the terminal runtime state.
def _resolve_terminal_state(task_state: RuntimeState) -> str | None:
    if task_state in {RuntimeState.COMPLETED, RuntimeState.ABORTED}:
        return task_state.value
    return None


# Resolve the current reason-code set for the dashboard header.
def _resolve_reason_codes(*, task_state: TaskState) -> tuple[str, ...]:
    pending_request = task_state.pending_human_review_request
    if pending_request is not None:
        return pending_request.reason_codes

    governance_decision = task_state.last_governance_decision
    if governance_decision is not None:
        return governance_reason_code_values(governance_decision)

    oversight_assessment = task_state.last_dynamic_policy_assessment or task_state.last_oversight_assessment
    if oversight_assessment is not None:
        return tuple(code.value for code in oversight_assessment.reason_codes)

    behavioral_assessment = task_state.last_behavioral_assessment
    if behavioral_assessment is not None:
        return behavioral_assessment.notes

    return tuple()


# Resolve the resume state when the runtime has not already stored one.
def _resolve_resume_state(task_state: TaskState) -> str | None:
    if task_state.human_review_resume_state is not None:
        return task_state.human_review_resume_state.value
    return None


# Reconstruct a readable review summary from the review history when needed.
def _build_review_summary_from_history(
    *,
    last_request: HumanReviewRequest | None,
    last_decision: HumanReviewDecision | None,
) -> str | None:
    parts: list[str] = []
    if last_request is not None and last_request.review_summary:
        parts.append(last_request.review_summary)
    if last_decision is not None:
        parts.append(f"Last human decision: {last_decision.decision_type.value}.")
    return " ".join(parts) if parts else None
