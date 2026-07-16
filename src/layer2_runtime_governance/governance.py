"""Legacy checkpoint type aliases for pre-modular governance snapshots.

New code must import from ``governance_components``. This module remains only
because durable checkpoints may reference its historical qualified class names.
"""

from .governance_components.contracts import (
    GovernanceDecision,
    GovernanceDecisionTrace,
    GovernanceDirective,
    GovernanceReasonCode,
    PolicyDecision,
    PolicyEvaluationInput,
    PolicyEvaluationResult,
    StaticPolicyAssessment,
)
from .governance_components.policy_engine import OPAHttpPolicyEngineSettings

__all__ = [
    "GovernanceDecision",
    "GovernanceDecisionTrace",
    "GovernanceDirective",
    "GovernanceReasonCode",
    "OPAHttpPolicyEngineSettings",
    "PolicyDecision",
    "PolicyEvaluationInput",
    "PolicyEvaluationResult",
    "StaticPolicyAssessment",
]
