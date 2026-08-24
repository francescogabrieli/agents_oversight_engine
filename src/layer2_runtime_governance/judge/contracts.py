from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from layer3_tool_bus_external_capabilities.external_operations.contracts import ExternalOperationProposal


class JudgeRecommendedDirective(str, Enum):
    # Deprecated compatibility vocabulary; OPA never consumes this field.
    CONTINUE = "continue"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    REPLAN = "replan"
    WAIT_HUMAN_REVIEW = "wait_human_review"
    NOT_INVOKED = "not_invoked"


class JudgeAssessmentStatus(str, Enum):
    ACTIVE = "active"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class JudgeVerdict(str, Enum):
    SAFE = "safe"
    NEEDS_REVISION = "needs_revision"
    UNSAFE = "unsafe"
    UNCERTAIN = "uncertain"


class JudgeClaimStatus(str, Enum):
    # Claim grounding is a typed semantic assessment, not an Evidence-pillar score.
    SUPPORTED_BY_SELECTED = "supported_by_selected"
    SUPPORTED_BUT_NOT_SELECTED = "supported_but_not_selected"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    CONTEXT_INCOMPLETE = "context_incomplete"


class JudgeSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class JudgeOperationStage(str, Enum):
    # Distinguish a reviewable communication draft from an operation ready to execute.
    COMMUNICATION_DRAFT_REVIEW = "communication_draft_review"
    EXECUTION_CANDIDATE = "execution_candidate"


class JudgeFindingCategory(str, Enum):
    GOAL_ALIGNMENT = "goal_alignment"
    FACTUAL_SUPPORT = "factual_support"
    COMPLETENESS = "completeness"
    INTERNAL_CONSISTENCY = "internal_consistency"
    TARGET_APPROPRIATENESS = "target_appropriateness"
    DECEPTION_OR_COERCION = "deception_or_coercion"
    OFFENSIVE_OR_HARMFUL_CONTENT = "offensive_or_harmful_content"
    SENSITIVE_INFORMATION = "sensitive_information"
    EXCESSIVE_OPERATION = "excessive_operation"
    DANGEROUS_AMBIGUITY = "dangerous_ambiguity"
    CONTENT_DESTINATION_MISMATCH = "content_destination_mismatch"
    OTHER = "other"


class JudgeTaskRequirementKind(str, Enum):
    # The four authoritative task surfaces available to a finding anchor.
    GOAL = "goal"
    CONTEXT = "context"
    CONSTRAINT = "constraint"
    SUCCESS_CRITERION = "success_criterion"


class JudgePayloadField(str, Enum):
    # Closed payload fields prevent a finding from naming an invented review surface.
    CHANNEL = "channel"
    RECIPIENT = "recipient"
    SUBJECT = "subject"
    BODY = "body"
    CC = "cc"
    BCC = "bcc"
    SQL = "sql"
    NORMALIZED_SQL = "normalized_sql"
    STATEMENT_CLASS = "statement_class"
    DATABASE_PROFILE_ID = "database_profile_id"
    PARAMETERS = "parameters"
    ACTION = "action"
    TARGET = "target"
    FIELDS = "fields"


@dataclass(frozen=True)
class JudgeTaskRequirement:
    # This exact task quote makes a semantic finding replayable and falsifiable.
    kind: JudgeTaskRequirementKind
    index: int
    quote: str


@dataclass(frozen=True)
class JudgeFinding:
    # Findings are concise semantic observations, never runtime directives.
    category: JudgeFindingCategory
    code: str
    severity: JudgeSeverity
    summary: str
    evidence_refs: tuple[str, ...] = ()
    task_requirement: JudgeTaskRequirement | None = None
    payload_field: JudgePayloadField | None = None
    payload_excerpt: str | None = None

    # Preserve tuple semantics when checkpoint serializers restore arrays as lists.
    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


@dataclass(frozen=True)
class JudgeClaimAssessment:
    # One concise payload claim and its semantic relation to the complete observation base.
    claim_summary: str
    status: JudgeClaimStatus
    selected_evidence_refs: tuple[str, ...] = ()
    supporting_evidence_refs: tuple[str, ...] = ()
    contradicting_evidence_refs: tuple[str, ...] = ()
    explanation: str = ""

    # Preserve immutable reference collections across checkpoint roundtrips.
    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "selected_evidence_refs",
            tuple(self.selected_evidence_refs),
        )
        object.__setattr__(
            self,
            "supporting_evidence_refs",
            tuple(self.supporting_evidence_refs),
        )
        object.__setattr__(
            self,
            "contradicting_evidence_refs",
            tuple(self.contradicting_evidence_refs),
        )


@dataclass(frozen=True)
class JudgePlanStepContext:
    # The evaluated step is selected by actor_result.plan_step_id.
    step_id: str
    objective: str
    rationale: str
    expected_outcome: str
    status: str | None = None


@dataclass(frozen=True)
class JudgeEvidenceContext:
    # Judge evidence contains concrete, audit-safe provenance observations.
    evidence_id: str
    locator: str | None
    content: str
    source_timestamp: str | None = None
    plan_step_id: str | None = None
    selected_by_actor: bool = False
    content_truncated: bool = False
    incomplete_marker: str | None = None


@dataclass(frozen=True)
class JudgeEvidenceBundle:
    # This is the exact, canonical evidence pool exposed to one Judge invocation.
    artifact_version: str
    bundle_digest: str
    external_operation_digest: str
    evidence_snapshot_digest: str
    evidence_selection_digest: str
    evidence_base_complete: bool
    observation_count: int
    included_observation_count: int
    omitted_observation_ids: tuple[str, ...]
    selected_evidence_refs: tuple[str, ...]
    allowed_evidence_ids: tuple[str, ...]
    observations: tuple[JudgeEvidenceContext, ...]

    # Preserve immutable collections after checkpoint or JSON restoration.
    def __post_init__(self) -> None:
        object.__setattr__(self, "omitted_observation_ids", tuple(self.omitted_observation_ids))
        object.__setattr__(self, "selected_evidence_refs", tuple(self.selected_evidence_refs))
        object.__setattr__(self, "allowed_evidence_ids", tuple(self.allowed_evidence_ids))
        object.__setattr__(self, "observations", tuple(self.observations))


@dataclass(frozen=True)
class JudgeCompletedStepContext:
    # Completed outputs show what the actor actually committed before the proposal.
    step_id: str
    objective: str
    result_type: str
    summary: str
    output_text: str | None


@dataclass(frozen=True)
class JudgeScratchpadContext:
    # Scratchpad entries are audit-safe summaries, never hidden chain-of-thought.
    turn_index: int
    plan_step_id: str
    entry_type: str
    summary: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class JudgeToolArgumentContext:
    # Tool arguments preserve the concrete read-only action without an open dict contract.
    name: str
    value: str


@dataclass(frozen=True)
class JudgeToolExecutionContext:
    # Tool history exposes both successful acquisition and observable failures.
    request_id: str
    plan_step_id: str
    tool_name: str
    reason: str
    arguments: tuple[JudgeToolArgumentContext, ...]
    success: bool
    summary: str
    content: str | None
    observation_ids: tuple[str, ...]
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True)
class JudgeRuntimeEventContext:
    # Ordered node events preserve the runtime path without infrastructure payload noise.
    sequence: int
    runtime_state: str
    node_name: str
    summary: str


@dataclass(frozen=True)
class JudgeExternalRevisionContext:
    # Prior proposal digests and feedback make semantic revisions visible to the judge.
    operation_id: str
    payload_digest: str
    payload_preview: str
    finding_codes: tuple[str, ...] = ()
    feedback_summary: str | None = None


@dataclass(frozen=True)
class JudgeStageContract:
    # Stage-local lifecycle facts are model-visible data rather than remote prose.
    # The external effect has not occurred at evaluation time in any stage; the
    # payload is always assessed before its governed side effect is applied.
    stage: JudgeOperationStage
    side_effect_occurred: bool
    completeness_definition: str
    out_of_scope_checks: tuple[str, ...] = ()

    # Preserve the closed check list through checkpoint restoration.
    def __post_init__(self) -> None:
        object.__setattr__(self, "out_of_scope_checks", tuple(self.out_of_scope_checks))


@dataclass(frozen=True)
class JudgeContext:
    # JudgeContext intentionally excludes static, dynamic, risk, and behavioral assessments.
    task_goal: str
    task_constraints: tuple[str, ...]
    authorized_context: str
    current_plan_step: JudgePlanStepContext
    external_operation: ExternalOperationProposal
    stage_contract: JudgeStageContract
    selected_evidence_refs: tuple[str, ...]
    observations: tuple[JudgeEvidenceContext, ...]
    # These legacy process fields are compatibility-only and are never serialized to the prompt.
    actor_self_confidence: float | None = None
    residual_uncertainty: str | None = None
    destination_facts: tuple[str, ...] = ()
    invocation_reason: str = "opa_admitted_governed_external_candidate"
    task_success_criteria: tuple[str, ...] = ()
    execution_intent: str = "proposal_only"
    full_plan: tuple[JudgePlanStepContext, ...] = ()
    completed_steps: tuple[JudgeCompletedStepContext, ...] = ()
    # Compatibility-only process narration remains empty in all new contexts.
    scratchpad_entries: tuple[JudgeScratchpadContext, ...] = ()
    tool_executions: tuple[JudgeToolExecutionContext, ...] = ()
    runtime_events: tuple[JudgeRuntimeEventContext, ...] = ()
    prior_external_revisions: tuple[JudgeExternalRevisionContext, ...] = ()
    external_communication_body: str | None = None
    evidence_base_complete: bool = True
    observation_count: int = 0
    included_observation_count: int = 0
    omitted_observation_ids: tuple[str, ...] = ()
    evidence_snapshot_digest: str | None = None
    evidence_selection_digest: str | None = None
    serialization_version: str = "judge_context.v6"
    # Built deterministically from the fields above and used as the sole model-visible pool.
    evidence_bundle: JudgeEvidenceBundle | None = None

    @property
    def operation_stage(self) -> JudgeOperationStage:
        # The stage contract is the single authoritative lifecycle source.
        return self.stage_contract.stage


@dataclass(frozen=True)
class JudgeModelMetadata:
    provider: str
    model: str
    judge_name: str


@dataclass(frozen=True)
class JudgeAssessment:
    # Legacy fields remain readable for checkpoint compatibility but are derived-only.
    invoked: bool
    recommendation: JudgeRecommendedDirective
    confidence: float
    summary: str
    rationale: str
    flags: tuple[str, ...]
    evaluated_target: str
    status: JudgeAssessmentStatus = JudgeAssessmentStatus.NOT_APPLICABLE
    verdict: JudgeVerdict | None = None
    severity: JudgeSeverity = JudgeSeverity.LOW
    findings: tuple[JudgeFinding, ...] = ()
    evaluated_operation_digest: str | None = None
    prompt_version: str | None = None
    model_metadata: JudgeModelMetadata | None = None
    claim_assessments: tuple[JudgeClaimAssessment, ...] = ()
    evaluated_evidence_snapshot_digest: str | None = None
    evaluated_evidence_selection_digest: str | None = None

    # Recover a semantic verdict for legacy checkpoints without making it authoritative.
    def __post_init__(self) -> None:
        object.__setattr__(self, "flags", tuple(self.flags))
        object.__setattr__(self, "findings", tuple(self.findings))
        object.__setattr__(
            self,
            "claim_assessments",
            tuple(self.claim_assessments),
        )
        if self.verdict is None:
            object.__setattr__(
                self,
                "verdict",
                {
                    JudgeRecommendedDirective.CONTINUE: JudgeVerdict.SAFE,
                    JudgeRecommendedDirective.REQUEST_MORE_EVIDENCE: JudgeVerdict.NEEDS_REVISION,
                    JudgeRecommendedDirective.REPLAN: JudgeVerdict.UNCERTAIN,
                    JudgeRecommendedDirective.WAIT_HUMAN_REVIEW: JudgeVerdict.UNSAFE,
                }[self.recommendation],
            )
        if self.invoked and self.status == JudgeAssessmentStatus.NOT_APPLICABLE:
            object.__setattr__(self, "status", JudgeAssessmentStatus.ACTIVE)


class JudgePort(Protocol):
    judge_name: str
    prompt_version: str

    # The judge receives only the semantic external-operation context.
    def evaluate(self, *, context: JudgeContext) -> JudgeAssessment: ...


class JudgeExecutionError(RuntimeError):
    pass


__all__ = [
    "JudgeAssessment",
    "JudgeAssessmentStatus",
    "JudgeClaimAssessment",
    "JudgeClaimStatus",
    "JudgeCompletedStepContext",
    "JudgeContext",
    "JudgeEvidenceBundle",
    "JudgeEvidenceContext",
    "JudgeExecutionError",
    "JudgeExternalRevisionContext",
    "JudgeFinding",
    "JudgeFindingCategory",
    "JudgeModelMetadata",
    "JudgeOperationStage",
    "JudgePayloadField",
    "JudgePlanStepContext",
    "JudgePort",
    "JudgeRecommendedDirective",
    "JudgeRuntimeEventContext",
    "JudgeScratchpadContext",
    "JudgeSeverity",
    "JudgeStageContract",
    "JudgeTaskRequirement",
    "JudgeTaskRequirementKind",
    "JudgeToolArgumentContext",
    "JudgeToolExecutionContext",
    "JudgeVerdict",
]
