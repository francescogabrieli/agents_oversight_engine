from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any

from .governance import GovernanceDecision
from .oversight import OversightAssessment
from .state import (
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewRequest,
    RuntimeState,
    TaskState,
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
    directive: str
    policy_decision: str
    reason_codes: tuple[str, ...]
    summary: str
    constrains_capability_use: bool
    policy_directive: str
    oversight_recommended_directive: str
    evaluated_actor_result_type: str
    evaluated_tool_name: str | None
    policy_engine_name: str
    policy_bundle_version: str
    oversight_summary: str | None


# DashboardOversightPanel exposes the deterministic adaptive oversight result.
@dataclass(frozen=True)
class DashboardOversightPanel:
    evidence_quality_score: float
    evidence_quality_level: str
    evidence_quality_reason_codes: tuple[str, ...]
    operational_risk_score: float
    operational_risk_level: str
    operational_risk_reason_codes: tuple[str, ...]
    trajectory_health_score: float
    trajectory_health_level: str
    trajectory_health_reason_codes: tuple[str, ...]
    recommended_directive: str
    summary: str
    reason_codes: tuple[str, ...]


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
    human_review: DashboardHumanReviewPanel | None
    reason_codes: tuple[str, ...]
    resume_state: str | None
    terminal_state: str | None
    has_pending_review: bool
    is_terminal: bool
    external_communication_state: str


# Build the dashboard snapshot from the canonical TaskState.
def build_governance_hitl_dashboard_snapshot(
    task_state: TaskState,
) -> GovernanceHitlDashboardSnapshot:
    current_state = task_state.current_state
    focus = _resolve_dashboard_focus(task_state=current_state)
    governance_panel = _build_governance_panel(task_state.last_governance_decision)
    oversight_panel = _build_oversight_panel(task_state.last_oversight_assessment)
    human_review_panel = _build_human_review_panel(task_state=task_state)
    current_plan_step = task_state.current_plan_step

    return GovernanceHitlDashboardSnapshot(
        dashboard_version="governance_hitl_dashboard_v1",
        focus=focus,
        runtime_state=current_state.value,
        runtime_thread_id=task_state.runtime_thread_id,
        current_plan_step=(
            _build_plan_step(current_plan_step) if current_plan_step is not None else None
        ),
        governance=governance_panel,
        oversight=oversight_panel,
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
        external_communication_state="not_implemented",
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
        directive=governance_decision.directive.value,
        policy_decision=governance_decision.policy_decision.value,
        reason_codes=tuple(code.value for code in governance_decision.reason_codes),
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
        oversight_summary=governance_decision.oversight_summary,
    )


# Build the latest deterministic oversight panel if one exists.
def _build_oversight_panel(
    oversight_assessment: OversightAssessment | None,
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
        trajectory_health_score=oversight_assessment.trajectory_health.score,
        trajectory_health_level=oversight_assessment.trajectory_health.level.value,
        trajectory_health_reason_codes=tuple(
            code.value for code in oversight_assessment.trajectory_health.reason_codes
        ),
        recommended_directive=oversight_assessment.recommended_directive.value,
        summary=oversight_assessment.summary,
        reason_codes=tuple(code.value for code in oversight_assessment.reason_codes),
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
        return tuple(code.value for code in governance_decision.reason_codes)

    oversight_assessment = task_state.last_oversight_assessment
    if oversight_assessment is not None:
        return tuple(code.value for code in oversight_assessment.reason_codes)

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


# Convert nested dataclasses and enums into JSON-safe primitives.
def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value

