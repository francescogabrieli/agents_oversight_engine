from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from ..external_operations.contracts import ExternalOperationProposal
from ..tool_contract import EvidenceSupportLevel


class JudgeRecommendedDirective(str, Enum):
    # Deprecated compatibility vocabulary; OPA never consumes this field.
    CONTINUE = "continue"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    REPLAN = "replan"
    WAIT_HUMAN_REVIEW = "wait_human_review"


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


class JudgeSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class JudgeFindingCategory(str, Enum):
    GOAL_ALIGNMENT = "goal_alignment"
    TARGET_APPROPRIATENESS = "target_appropriateness"
    DECEPTION_OR_COERCION = "deception_or_coercion"
    OFFENSIVE_OR_HARMFUL_CONTENT = "offensive_or_harmful_content"
    SENSITIVE_INFORMATION = "sensitive_information"
    EXCESSIVE_OPERATION = "excessive_operation"
    DANGEROUS_AMBIGUITY = "dangerous_ambiguity"
    CONTENT_DESTINATION_MISMATCH = "content_destination_mismatch"
    OTHER = "other"


@dataclass(frozen=True)
class JudgeFinding:
    # Findings are concise semantic observations, never runtime directives.
    category: JudgeFindingCategory
    code: str
    severity: JudgeSeverity
    summary: str
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class JudgePlanStepContext:
    # The evaluated step is selected by actor_result.plan_step_id.
    step_id: str
    objective: str
    rationale: str
    expected_outcome: str


@dataclass(frozen=True)
class JudgeEvidenceContext:
    # Judge evidence contains only audit-safe excerpts and support metadata.
    evidence_id: str
    title: str
    locator: str
    excerpt: str
    support_level: EvidenceSupportLevel


@dataclass(frozen=True)
class JudgeContext:
    # JudgeContext intentionally excludes static, dynamic, risk, and behavioral assessments.
    task_goal: str
    task_constraints: tuple[str, ...]
    authorized_context: str
    current_plan_step: JudgePlanStepContext
    external_operation: ExternalOperationProposal
    observed_evidence: tuple[JudgeEvidenceContext, ...]
    available_evidence_support_level: EvidenceSupportLevel
    required_evidence_support_level: EvidenceSupportLevel
    destination_facts: tuple[str, ...]
    invocation_reason: str


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

    # Recover a semantic verdict for legacy checkpoints without making it authoritative.
    def __post_init__(self) -> None:
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
    "JudgeContext",
    "JudgeEvidenceContext",
    "JudgeExecutionError",
    "JudgeFinding",
    "JudgeFindingCategory",
    "JudgeModelMetadata",
    "JudgePlanStepContext",
    "JudgePort",
    "JudgeRecommendedDirective",
    "JudgeSeverity",
    "JudgeVerdict",
]
