
from dataclasses import dataclass
from typing import Any




# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TraceRuntimeSummary:
    # TraceRuntimeSummary identifies the run and its terminal runtime state.
    thread_id: str
    current_state: str
    bootstrap_completed: bool
    runtime_flags: tuple[str, ...]
    step_history: tuple[dict[str, Any], ...]
    actor_turn_count: int
    tool_call_count: int


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TraceIntakeSummary:
    # TraceIntakeSummary preserves the Layer 1 handoff in compact audit form.
    goal: str
    context: str
    domain_type: str
    domain_label: str
    sensitivity: str
    success_criteria: tuple[str, ...]
    constraints: tuple[str, ...]
    parse_status: str
    parser_name: str
    prompt_version: str
    schema_version: str
    policy_profile_name: str
    confidence_threshold: float
    max_steps: int | None
    max_tool_calls: int | None
    read_only_mode: bool


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TracePlanStep:
    # TracePlanStep is one semantic planner step as seen by runtime governance.
    step_id: str
    objective: str
    rationale: str
    expected_outcome: str
    step_kind: str | None
    evidence_requirement: str | None
    claim_precision_ceiling: str | None
    resolved_required_evidence_support_level: str | None
    resolved_required_evidence_support_source: str | None
    status: str


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TraceActorTurn:
    # TraceActorTurn captures actor-facing execution without hidden reasoning.
    actor_name: str | None
    actor_prompt_version: str | None
    result_type: str | None
    plan_step_id: str | None
    summary: str | None
    capability_mode: str | None
    external_impact: bool | None
    output_text: str | None
    blocking_reason: str | None
    proposed_tool_name: str | None
    output_claim_support_level: str | None
    precision_mismatch_detected: bool


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TraceToolCall:
    # TraceToolCall records the tool request/result material retained by TaskState.
    plan_step_id: str
    tool_name: str
    reason: str
    query: str | None
    url: str | None
    text: str | None
    path: str | None
    expression: str | None
    timezone: str | None
    limit: int | None
    max_results: int
    extractive_goal: str | None
    success: bool | None
    summary: str | None
    content: str | None
    error_message: str | None
    sources: tuple[dict[str, Any], ...]


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TraceObservedEvidence:
    # TraceObservedEvidence is the grounded evidence surface visible to later actor turns.
    evidence_id: str
    plan_step_id: str
    title: str
    locator: str
    excerpt: str
    source_kind: str
    source_tool: str | None
    support_level: str


# Read-only canonical governance record; pillar outputs are stored exactly once per pass.
@dataclass(frozen=True)
class TraceGovernancePass:
    pass_index: int
    evaluated_plan_step_id: str | None
    evaluated_actor_result_type: str
    evaluated_tool_name: str | None
    governance_input_schema_version: str | None
    policy_bundle_version: str
    policy_config_version: str | None
    pillar_requirements: dict[str, bool]
    pillar_results: dict[str, Any]
    pillar_errors: dict[str, str]
    directive: str
    policy_decision: str
    requires_human_review: bool
    reason_codes: tuple[str, ...]
    selected_reason: str
    rejected_alternatives: dict[str, str]
    safety_invariants: tuple[str, ...]


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TraceOversightSignal:
    # TraceOversightSignal keeps each adaptive score separate and explainable.
    score: float
    level: str
    reason_codes: tuple[str, ...]


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TraceOversightAssessment:
    # TraceOversightAssessment records the confidence/risk/trajectory recommendation.
    evidence_quality: TraceOversightSignal
    operational_risk: TraceOversightSignal
    trajectory_health: TraceOversightSignal
    recommended_directive: str
    summary: str
    reason_codes: tuple[str, ...]


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TraceStaticPolicyAssessment:
    # TraceStaticPolicyAssessment keeps the OPA hard decision separate from later layers.
    policy_decision: str
    reason_codes: tuple[str, ...]
    summary: str
    policy_engine_name: str
    policy_bundle_version: str
    policy_directive: str


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TraceBehavioralAssessment:
    # TraceBehavioralAssessment exposes the behavioural history summary, scores, and advisory directive.
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


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TraceJudgeAssessment:
    # TraceJudgeAssessment keeps the selective semantic judge auditable across governance passes.
    invoked: bool
    recommendation: str
    confidence: float
    summary: str
    rationale: str
    flags: tuple[str, ...]
    evaluated_target: str


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TraceExternalCommunicationDraft:
    # TraceExternalCommunicationDraft captures the typed outbound content in JSON-safe form.
    channel: str
    recipient: str | None
    subject: str | None
    body: str | None
    metadata: dict[str, Any]


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TraceHumanReviewExternalEdit:
    # TraceHumanReviewExternalEdit captures a human-applied draft edit for audit.
    draft: TraceExternalCommunicationDraft
    note: str | None


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TraceHumanReviewRequest:
    # TraceHumanReviewRequest captures the pending HITL payload in JSON-safe form.
    request_id: str
    thread_id: str
    current_state: str
    resume_state: str
    current_plan_step_id: str | None
    current_plan_step_objective: str | None
    actor_result_type: str | None
    current_tool_request: dict[str, Any] | None
    governance_directive: str | None
    governance_summary: str | None
    static_policy_summary: str | None
    dynamic_policy_summary: str | None
    behavioral_summary: str | None
    judge_summary: str | None
    oversight_summary: str | None
    external_communication_target_type: str | None
    external_communication_draft: TraceExternalCommunicationDraft | None
    reason_codes: tuple[str, ...]
    allowed_decisions: tuple[str, ...]
    review_summary: str | None


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TraceHumanReviewDecision:
    # TraceHumanReviewDecision captures the human resume payload and history.
    request_id: str
    decision_type: str
    note: str | None
    clarification_text: str | None
    external_communication_edit: TraceHumanReviewExternalEdit | None
    reviewer_id: str | None


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TraceHumanReviewSummary:
    # TraceHumanReviewSummary keeps the pending review and history easy to inspect.
    pending_request: TraceHumanReviewRequest | None
    last_request: TraceHumanReviewRequest | None
    last_decision: TraceHumanReviewDecision | None
    decision_history: tuple[TraceHumanReviewDecision, ...]


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TraceExecutionSummary:
    # TraceExecutionSummary gathers final output and audit-safe execution history.
    final_output_text: str | None
    executed_steps: tuple[dict[str, Any], ...]
    scratchpad: tuple[dict[str, Any], ...]
    step_validations: tuple[dict[str, Any], ...]


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class TraceExternalCommunicationSummary:
    # Keep the current external communication phase visible in runtime artifacts and evaluation.
    phase: str
    draft: TraceExternalCommunicationDraft | None
    last_applied_edit: TraceHumanReviewExternalEdit | None
    edit_history: tuple[TraceHumanReviewExternalEdit, ...]
    last_transition_summary: str | None


# Read-only observability component extracted from the trace facade.
@dataclass(frozen=True)
class Layer2RuntimeTrace:
    # Layer2RuntimeTrace is the stable JSON-exportable audit artifact for Layer 2.
    trace_version: str
    runtime: TraceRuntimeSummary
    intake: TraceIntakeSummary
    planner_name: str | None
    planner_prompt_version: str | None
    planner_rationale: str | None
    planning_source: str | None
    plan_steps: tuple[TracePlanStep, ...]
    current_plan_step_index: int | None
    current_plan_step_id: str | None
    actor: TraceActorTurn
    execution: TraceExecutionSummary
    tool_calls: tuple[TraceToolCall, ...]
    observed_evidence: tuple[TraceObservedEvidence, ...]
    governance_passes: tuple[TraceGovernancePass, ...]
    human_review: TraceHumanReviewSummary
    external_communication: TraceExternalCommunicationSummary | None


__all__ = [
    "TraceRuntimeSummary",
    "TraceIntakeSummary",
    "TracePlanStep",
    "TraceActorTurn",
    "TraceToolCall",
    "TraceObservedEvidence",
    "TraceGovernancePass",
    "TraceOversightSignal",
    "TraceOversightAssessment",
    "TraceStaticPolicyAssessment",
    "TraceBehavioralAssessment",
    "TraceJudgeAssessment",
    "TraceExternalCommunicationDraft",
    "TraceHumanReviewExternalEdit",
    "TraceHumanReviewRequest",
    "TraceHumanReviewDecision",
    "TraceHumanReviewSummary",
    "TraceExecutionSummary",
    "TraceExternalCommunicationSummary",
    "Layer2RuntimeTrace"
]
