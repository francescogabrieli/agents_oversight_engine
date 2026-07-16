from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from layer1_intake import DomainType, SensitivityLevel

from ..actor_contract import ActorResultType, CapabilityAccessMode
from ..behavioral import BehavioralAssessment
from ..judge import JudgeAssessment
from ..oversight import DynamicPolicyAssessment
from ..domain import ExternalCommunicationPhase, RuntimeFlag, RuntimeState
from ..tool_contract import ToolName

# PolicyDecision is the deterministic outcome returned by the policy engine.
# Define the stable typed vocabulary shared by runtime and policy adapters.
class PolicyDecision(str, Enum):
    ALLOW = "allow"
    ALLOW_READ_ONLY = "allow_read_only"
    ASK_CLARIFICATION = "ask_clarification"
    REQUIRE_HUMAN_REVIEW = "require_human_review"
    DENY = "deny"

# Define the stable typed vocabulary shared by runtime and policy adapters.
class GovernanceDirective(str, Enum):
    CONTINUE = "continue"
    CONTINUE_READ_ONLY = "continue_read_only"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    REPLAN = "replan"
    ASK_USER_CLARIFICATION = "ask_user_clarification"
    WAIT_HUMAN_REVIEW = "wait_human_review"
    STOP = "stop"

# Define the stable typed vocabulary shared by runtime and policy adapters.
class GovernanceReasonCode(str, Enum):
    ALLOWED_BY_POLICY = "allowed_by_policy"
    READ_ONLY_ALLOWED = "read_only_allowed"
    WRITE_CAPABILITY_BLOCKED = "write_capability_blocked"
    READ_ONLY_MODE_ENFORCED = "read_only_mode_enforced"
    LOCAL_OUTPUT_ALLOWED = "local_output_allowed"
    REPLAN_ALLOWED = "replan_allowed"
    ASK_CLARIFICATION_REQUIRED = "ask_clarification_required"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    DENIED_BY_POLICY = "denied_by_policy"
    NO_TOOL_REQUEST = "no_tool_request"
    EXTERNAL_CAPABILITY_REQUESTED = "external_capability_requested"
    EXTERNAL_COMMUNICATION_REVIEW_REQUIRED = "external_communication_review_required"
    EVIDENCE_SUPPORT_BELOW_STEP_NEED = "evidence_support_below_step_need"
    PRECISION_MISMATCH = "precision_mismatch"
    TRAJECTORY_DEGRADED = "trajectory_degraded"
    EVIDENCE_GAP_AND_TRAJECTORY_DEGRADED = "evidence_gap_and_trajectory_degraded"
    HIGH_OPERATIONAL_RISK_EXTERNAL = "high_operational_risk_external"
    REPLAN_LIMIT_EXCEEDED = "replan_limit_exceeded"
    PROVIDER_FAILURES_REPEATED = "provider_failures_repeated"
    REPEATED_RECOVERY = "repeated_recovery"
    BEHAVIORAL_ANOMALY_NEEDS_CORROBORATION = "behavioral_anomaly_needs_corroboration"
    BEHAVIORAL_CORROBORATED_RISK = "behavioral_corroborated_risk"
    JUDGE_REJECT_OUTPUT = "judge_reject_output"
    JUDGE_REJECT_EXTERNAL_OR_HIGH_RISK = "judge_reject_external_or_high_risk"
    JUDGE_FLAG_EXTERNAL_DRAFT = "judge_flag_external_draft"
    POLICY_VERSION_MISMATCH = "policy_version_mismatch"
    REQUIRED_PILLAR_UNAVAILABLE = "required_pillar_unavailable"
    OPA_FAILURE_FAIL_CLOSED = "opa_failure_fail_closed"

# Define the stable typed vocabulary shared by runtime and policy adapters.
@dataclass(frozen=True)
class PolicyEvaluationInput:
    # PolicyEvaluationInput is the compact runtime snapshot sent to governance layers.
    task_domain_type: DomainType
    task_domain_label: str
    task_sensitivity: SensitivityLevel
    policy_profile_name: str
    confidence_threshold: float
    read_only_mode: bool
    budget_max_steps: int | None
    budget_max_tool_calls: int | None
    runtime_state: RuntimeState
    runtime_flags: tuple[RuntimeFlag, ...]
    current_plan_step_id: str | None
    current_plan_step_objective: str | None
    actor_result_type: ActorResultType
    capability_mode: CapabilityAccessMode
    external_impact: bool
    blocking_reason: str | None
    actor_has_output_text: bool
    tool_name: ToolName | None
    tool_has_query: bool
    tool_has_url: bool
    tool_call_count: int
    observed_evidence_count: int
    actor_turn_count: int
    external_communication_phase: ExternalCommunicationPhase | None
    external_communication_review_ready: bool

# Define the stable typed vocabulary shared by runtime and policy adapters.
@dataclass(frozen=True)
class PolicyEvaluationResult:
    # PolicyEvaluationResult mirrors the raw typed output returned by OPA.
    policy_decision: PolicyDecision
    reason_codes: tuple[GovernanceReasonCode, ...]
    summary: str
    policy_engine_name: str
    policy_bundle_version: str

# Define the stable typed vocabulary shared by runtime and policy adapters.
@dataclass(frozen=True)
class StaticPolicyAssessment:
    # StaticPolicyAssessment is the hard deterministic result produced by OPA.
    policy_decision: PolicyDecision
    reason_codes: tuple[GovernanceReasonCode, ...]
    summary: str
    policy_engine_name: str
    policy_bundle_version: str
    policy_directive: GovernanceDirective

# Define the stable typed vocabulary shared by runtime and policy adapters.
@dataclass(frozen=True)
class GovernanceDecisionTrace:
    # GovernanceDecisionTrace is the canonical combined governance record stored in TaskState.
    directive: GovernanceDirective
    policy_decision: PolicyDecision
    reason_codes: tuple[GovernanceReasonCode, ...]
    summary: str
    constrains_capability_use: bool
    evaluated_actor_result_type: ActorResultType
    evaluated_tool_name: ToolName | None
    policy_engine_name: str
    policy_bundle_version: str
    policy_directive: GovernanceDirective
    oversight_recommended_directive: GovernanceDirective
    behavioral_recommended_directive: GovernanceDirective = GovernanceDirective.CONTINUE
    judge_recommended_directive: GovernanceDirective = GovernanceDirective.CONTINUE
    judge_invoked: bool = False
    static_policy_summary: str | None = None
    dynamic_policy_summary: str | None = None
    behavioral_summary: str | None = None
    judge_summary: str | None = None
    behavioral_similarity_score: float | None = None
    behavioral_deviation_score: float | None = None
    behavioral_novelty_score: float | None = None
    oversight_summary: str | None = None
    combiner_dominant_dimension: str | None = None
    combiner_support_by_directive: dict[str, float] | None = None
    combiner_selected_reason: str | None = None
    combiner_rejected_alternatives: dict[str, str] | None = None
    combiner_safety_invariants_checked: tuple[str, ...] = ()
    combiner_signals: tuple[dict[str, Any], ...] = ()
    combiner_normalized_directive: str | None = None
    combiner_normalization_reason: str | None = None
    governance_input_schema_version: str | None = None
    pillar_requirements: dict[str, bool] | None = None
    pillar_results: dict[str, Any] | None = None
    pillar_errors: dict[str, str] | None = None
    policy_config_version: str | None = None

# Define the stable typed vocabulary shared by runtime and policy adapters.
@dataclass(frozen=True)
class GovernanceEvaluationResult:
    # GovernanceEvaluationResult carries all runtime records produced by the OPA-first PEP flow.
    policy_input: PolicyEvaluationInput
    static_policy_assessment: StaticPolicyAssessment
    dynamic_policy_assessment: DynamicPolicyAssessment
    behavioral_assessment: BehavioralAssessment
    judge_assessment: JudgeAssessment
    governance_decision: GovernanceDecisionTrace
    decision_input: dict[str, Any]
    pillar_requirements: dict[str, bool]

# Define the stable typed vocabulary shared by runtime and policy adapters.
class PolicyEnginePort(Protocol):
    policy_engine_name: str
    policy_bundle_version: str
    policy_config_version: str

    def evaluate_required_pillars(self, *, base_facts: dict[str, Any]) -> dict[str, bool]: ...

    def evaluate_decision(self, *, decision_input: dict[str, Any]) -> dict[str, Any]: ...

# Define the stable typed vocabulary shared by runtime and policy adapters.
class PolicyEngineExecutionError(RuntimeError):
    pass

# Keep the legacy public name stable while the internal model stays explicit.
GovernanceDecision = GovernanceDecisionTrace

__all__ = [
    "PolicyDecision",
    "GovernanceDirective",
    "GovernanceReasonCode",
    "PolicyEvaluationInput",
    "PolicyEvaluationResult",
    "StaticPolicyAssessment",
    "GovernanceDecisionTrace",
    "GovernanceEvaluationResult",
    "PolicyEnginePort",
    "PolicyEngineExecutionError"
]
