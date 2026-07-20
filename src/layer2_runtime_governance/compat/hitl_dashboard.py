"""Temporary HITL dashboard import aliases."""

from ..hitl.dashboard import (
    DashboardBehavioralPanel,
    DashboardExternalCommunicationPanel,
    DashboardFocus,
    DashboardGovernancePanel,
    DashboardHumanReviewPanel,
    DashboardOversightPanel,
    DashboardPlanStep,
    GovernanceHitlDashboardSnapshot,
    build_governance_hitl_dashboard_snapshot,
    governance_hitl_dashboard_snapshot_to_dict,
    governance_hitl_dashboard_snapshot_to_json,
)

__all__ = [
    "DashboardBehavioralPanel",
    "DashboardExternalCommunicationPanel",
    "DashboardFocus",
    "DashboardGovernancePanel",
    "DashboardHumanReviewPanel",
    "DashboardOversightPanel",
    "DashboardPlanStep",
    "GovernanceHitlDashboardSnapshot",
    "build_governance_hitl_dashboard_snapshot",
    "governance_hitl_dashboard_snapshot_to_dict",
    "governance_hitl_dashboard_snapshot_to_json",
]
