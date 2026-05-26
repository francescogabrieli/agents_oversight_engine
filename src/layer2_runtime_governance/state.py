from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from layer1_intake import (
    DomainType,
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    TaskSpec,
)

from .tool_contract import (
    EvidenceSourceKind,
    EvidenceSupportLevel,
    ObservedEvidence,
    ToolExecutionResult,
    ToolRequest,
)

if TYPE_CHECKING:
    from .actor_contract import ActorResult, ActorResultType
    from .behavioral import BehavioralAssessment
    from .governance import GovernanceDirective, GovernanceDecisionTrace, StaticPolicyAssessment
    from .judge import JudgeAssessment
    from .oversight import DynamicPolicyAssessment, OversightAssessment


# Layer 2 currently covers bootstrap, planning, and the actor/tool observation loop.
# This file keeps the canonical runtime state shared by the graph nodes.
class RuntimeState(str, Enum):
    PLANNING = "planning"
    ACTING = "acting"
    READY_NEXT_STEP = "ready_next_step"
    READY_TOOL_EXECUTION = "ready_tool_execution"
    REPLANNING = "replanning"
    WAITING_HUMAN_REVIEW = "waiting_human_review"
    COMPLETED = "completed"
    ABORTED = "aborted"


class RuntimeFlag(str, Enum):
    CLARIFICATION_REQUIRED = "clarification_required"
    INCOMPLETE_INTAKE = "incomplete_intake"
    READ_ONLY_EXECUTION = "read_only_execution"
    HIGH_SENSITIVITY = "high_sensitivity"
    EXTRACTABILITY_LIMITED = "extractability_limited"


class PlanStepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class ScratchpadEntryType(str, Enum):
    # These are audit categories, not hidden reasoning transcripts.
    OBSERVATION = "observation"
    TOOL_DECISION = "tool_decision"
    PROGRESS_NOTE = "progress_note"
    UNCERTAINTY = "uncertainty"
    COMPLETION_RATIONALE = "completion_rationale"


class StepValidationDecision(str, Enum):
    # Validation decisions let the actor self-check without directly mutating graph routing.
    COMPLETE_STEP = "complete_step"
    CONTINUE_ACTING = "continue_acting"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    ASK_CLARIFICATION = "ask_clarification"
    NEEDS_REPLAN = "needs_replan"


class HumanReviewDecisionType(str, Enum):
    # Human review stays typed so the runtime can pause and resume deterministically.
    APPROVE_CONTINUE = "approve_continue"
    APPROVE_CONTINUE_READ_ONLY = "approve_continue_read_only"
    MODIFY_AND_CONTINUE = "modify_and_continue"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    REPLAN = "replan"
    ASK_CLARIFICATION = "ask_clarification"
    ABORT = "abort"


class ExternalCommunicationPhase(str, Enum):
    # External communication remains a gated subflow, even before real execution is added.
    EVIDENCE_COLLECTION = "evidence_collection"
    DRAFT_IN_PROGRESS = "draft_in_progress"
    DRAFT_PROPOSAL = "draft_proposal"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    APPROVED_FOR_EXECUTION = "approved_for_execution"
    BLOCKED = "blocked"


@dataclass
class StepRecord:
    # This is the minimal audit entry recorded while Layer 2 performs runtime transitions.
    step_index: int
    runtime_state: RuntimeState
    node_name: str
    summary: str


@dataclass
class PlanStep:
    # Planner output stays semantic so actor can later choose the concrete action.
    step_id: str
    objective: str
    rationale: str
    expected_outcome: str
    status: PlanStepStatus = PlanStepStatus.PENDING


@dataclass
class PlannerDecision:
    # The planner returns a typed semantic planning snapshot plus traceability metadata.
    plan_steps: list[PlanStep]
    rationale: str
    planner_name: str
    prompt_version: str


@dataclass
class ExecutedPlanStep:
    # Keep a compact trace of each plan step the actor has already completed.
    step_id: str
    objective: str
    result_type: ActorResultType
    summary: str
    output_text: str | None = None


@dataclass
class ScratchpadEntry:
    # Store audit-safe working memory without preserving hidden chain-of-thought.
    turn_index: int
    plan_step_id: str
    entry_type: ScratchpadEntryType
    summary: str
    evidence_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StepValidation:
    # Capture the actor self-check used before the runtime completes a semantic step.
    plan_step_id: str
    objective_satisfied: bool
    evidence_sufficient: bool
    confidence: float
    next_action: StepValidationDecision
    rationale_summary: str
    residual_uncertainty: str | None = None


@dataclass(frozen=True)
class ExternalCommunicationDraft:
    # Keep the external payload explicit so HITL can edit the real outbound content.
    channel: str
    recipient: str | None = None
    subject: str | None = None
    body: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HumanReviewExternalEdit:
    # Human edits are carried as a typed full draft, not as a free-form patch.
    draft: ExternalCommunicationDraft
    note: str | None = None


@dataclass(frozen=True)
class HumanReviewRequest:
    # Human review requests are persisted so interrupts can resume on the same checkpoint.
    request_id: str
    thread_id: str
    current_state: RuntimeState
    resume_state: RuntimeState
    current_plan_step_id: str | None
    current_plan_step_objective: str | None
    actor_result_type: ActorResultType | None
    current_tool_request: ToolRequest | None
    governance_directive: GovernanceDirective | None
    governance_summary: str | None
    static_policy_summary: str | None = None
    dynamic_policy_summary: str | None = None
    behavioral_summary: str | None = None
    judge_summary: str | None = None
    oversight_summary: str | None = None
    external_communication_target_type: str | None = None
    external_communication_draft: ExternalCommunicationDraft | None = None
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    allowed_decisions: tuple[HumanReviewDecisionType, ...] = field(default_factory=tuple)
    review_summary: str | None = None


@dataclass(frozen=True)
class HumanReviewDecision:
    # Human review decisions are canonical and typed for audit-safe resume.
    request_id: str
    decision_type: HumanReviewDecisionType
    note: str | None = None
    clarification_text: str | None = None
    external_communication_edit: HumanReviewExternalEdit | None = None
    reviewer_id: str | None = None


@dataclass
class ExternalCommunicationState:
    # Keep the externally impactful draft/review boundary explicit in TaskState.
    phase: ExternalCommunicationPhase
    review_ready: bool = False
    draft: ExternalCommunicationDraft | None = None
    last_applied_edit: HumanReviewExternalEdit | None = None
    edit_history: list[HumanReviewExternalEdit] = field(default_factory=list)
    last_transition_summary: str | None = None


@dataclass
class TaskState:
    # Direct handoff from Layer 1.
    intake_payload: IntakeResult
    task_spec: TaskSpec
    execution_policy: ExecutionPolicy
    intake_metadata: IntakeMetadata

    # Bootstrap outcome.
    current_state: RuntimeState
    runtime_thread_id: str | None = None
    step_index: int = 0
    step_history: list[StepRecord] = field(default_factory=list)
    runtime_flags: list[RuntimeFlag] = field(default_factory=list)
    bootstrap_completed: bool = False
    human_review_resume_state: RuntimeState | None = None
    external_communication: ExternalCommunicationState | None = None

    # Planner snapshot.
    plan_steps: list[PlanStep] = field(default_factory=list)
    current_plan_step_index: int | None = None
    planner_rationale: str | None = None
    planner_name: str | None = None
    planner_prompt_version: str | None = None

    # Actor execution snapshot.
    actor_result: ActorResult | None = None
    actor_name: str | None = None
    actor_prompt_version: str | None = None
    executed_steps: list[ExecutedPlanStep] = field(default_factory=list)
    final_output_text: str | None = None
    actor_turn_count: int = 0
    # Scratchpad is intentionally summarized working memory, safe for audit and HITL display.
    scratchpad: list[ScratchpadEntry] = field(default_factory=list)
    # Step validations are the bridge toward confidence/self-stop logic.
    step_validations: list[StepValidation] = field(default_factory=list)

    # Tool loop snapshot.
    current_tool_request: ToolRequest | None = None
    last_tool_result: ToolExecutionResult | None = None
    tool_results_history: list[ToolExecutionResult] = field(default_factory=list)
    observed_evidence: list[ObservedEvidence] = field(default_factory=list)
    tool_call_count: int = 0

    # Governance snapshot.
    last_static_policy_assessment: StaticPolicyAssessment | None = None
    static_policy_history: list[StaticPolicyAssessment] = field(default_factory=list)
    last_dynamic_policy_assessment: DynamicPolicyAssessment | None = None
    dynamic_policy_history: list[DynamicPolicyAssessment] = field(default_factory=list)
    last_behavioral_assessment: BehavioralAssessment | None = None
    behavioral_history: list[BehavioralAssessment] = field(default_factory=list)
    last_judge_assessment: JudgeAssessment | None = None
    judge_history: list[JudgeAssessment] = field(default_factory=list)
    last_governance_decision: GovernanceDecisionTrace | None = None
    governance_history: list[GovernanceDecisionTrace] = field(default_factory=list)
    # Keep the legacy oversight fields populated until all dependent surfaces migrate.
    last_oversight_assessment: OversightAssessment | None = None
    oversight_history: list[OversightAssessment] = field(default_factory=list)

    # Human-in-the-loop snapshot.
    pending_human_review_request: HumanReviewRequest | None = None
    last_human_review_request: HumanReviewRequest | None = None
    human_review_history: list[HumanReviewDecision] = field(default_factory=list)
    last_human_review_decision: HumanReviewDecision | None = None

    @property
    def current_plan_step(self) -> PlanStep | None:
        # Expose the current semantic step without duplicating selection logic across nodes.
        if self.current_plan_step_index is None:
            return None

        if 0 <= self.current_plan_step_index < len(self.plan_steps):
            return self.plan_steps[self.current_plan_step_index]

        return None

    @classmethod
    def from_intake(cls, intake_result: IntakeResult) -> "TaskState":
        # Convert the Layer 1 payload into the first canonical Layer 2 state.
        parse_status = intake_result.metadata.parse_status
        runtime_flags: list[RuntimeFlag] = []

        # Parse status decides the first runtime state and any initial caution flags.
        if parse_status == ParseStatus.NEEDS_CLARIFICATION:
            current_state = RuntimeState.WAITING_HUMAN_REVIEW
            runtime_flags.append(RuntimeFlag.CLARIFICATION_REQUIRED)
        elif parse_status == ParseStatus.PARTIAL:
            current_state = RuntimeState.PLANNING
            runtime_flags.append(RuntimeFlag.INCOMPLETE_INTAKE)
        else:
            current_state = RuntimeState.PLANNING

        # Policy and sensitivity are also captured immediately so the handoff is self-contained.
        if intake_result.structured_task.execution_policy.read_only_mode:
            runtime_flags.append(RuntimeFlag.READ_ONLY_EXECUTION)

        if intake_result.structured_task.task_spec.sensitivity in {
            SensitivityLevel.HIGH,
            SensitivityLevel.CRITICAL,
        }:
            runtime_flags.append(RuntimeFlag.HIGH_SENSITIVITY)

        return cls(
            intake_payload=intake_result,
            task_spec=intake_result.structured_task.task_spec,
            execution_policy=intake_result.structured_task.execution_policy,
            intake_metadata=intake_result.metadata,
            current_state=current_state,
            runtime_flags=runtime_flags,
            human_review_resume_state=(
                RuntimeState.PLANNING
                if current_state == RuntimeState.WAITING_HUMAN_REVIEW
                else None
            ),
            external_communication=(
                ExternalCommunicationState(
                    phase=ExternalCommunicationPhase.EVIDENCE_COLLECTION,
                    last_transition_summary=(
                        "External communication tasks start in evidence collection until a draft "
                        "and typed review gate are reached."
                    ),
                )
                if intake_result.structured_task.task_spec.domain_type
                == DomainType.EXTERNAL_COMMUNICATION
                else None
            ),
            observed_evidence=_build_initial_observed_evidence_from_intake(
                intake_result.structured_task.task_spec
            ),
        )


# Materialize task-provided facts as initial runtime evidence for later local transformations.
def _build_initial_observed_evidence_from_intake(task_spec: TaskSpec) -> list[ObservedEvidence]:
    evidence_items: list[ObservedEvidence] = []
    seen: set[str] = set()
    candidate_index = 0

    # Goal text can carry the primary instruction or factual frame for local drafting tasks.
    for title, raw_text in (
        ("Task goal", task_spec.goal),
        ("Task context", task_spec.context),
    ):
        for statement in _split_intake_evidence_statements(raw_text):
            candidate_index += 1
            _append_intake_evidence_item(
                evidence_items=evidence_items,
                seen=seen,
                evidence_id=f"intake:{candidate_index}",
                plan_step_id="intake",
                title=title,
                excerpt=statement,
            )

    # Constraints and success criteria often encode authoritative task facts and operator requirements.
    for label, items in (
        ("Task constraint", task_spec.constraints),
        ("Success criterion", task_spec.success_criteria),
    ):
        for item in items:
            candidate_index += 1
            _append_intake_evidence_item(
                evidence_items=evidence_items,
                seen=seen,
                evidence_id=f"intake:{candidate_index}",
                plan_step_id="intake",
                title=label,
                excerpt=item,
            )

    return evidence_items


# Keep only factual or operationally authoritative intake statements as runtime evidence.
def _append_intake_evidence_item(
    *,
    evidence_items: list[ObservedEvidence],
    seen: set[str],
    evidence_id: str,
    plan_step_id: str,
    title: str,
    excerpt: str,
) -> None:
    normalized_excerpt = " ".join(excerpt.split())
    if not normalized_excerpt:
        return
    if not _is_authoritative_intake_evidence_text(normalized_excerpt):
        return

    dedupe_key = normalized_excerpt.lower()
    if dedupe_key in seen:
        return
    seen.add(dedupe_key)

    evidence_items.append(
        ObservedEvidence(
            evidence_id=evidence_id,
            plan_step_id=plan_step_id,
            title=title,
            locator="intake://task_spec",
            excerpt=normalized_excerpt[:1000],
            source_kind=EvidenceSourceKind.INTAKE_PROVIDED,
            source_tool=None,
            support_level=EvidenceSupportLevel.LOCAL_ONLY,
        )
    )


# Split semi-structured intake text into compact, separately traceable evidence statements.
def _split_intake_evidence_statements(text: str) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        return []
    parts = re.split(r"\s*[;•]\s*|\.\s+(?=[A-Z0-9])", normalized)
    return [part.strip(" .") for part in parts if part and part.strip(" .")]


# Exclude generic meta preferences while preserving factual and operational task inputs.
def _is_authoritative_intake_evidence_text(text: str) -> bool:
    lowered = text.lower()
    if lowered in {"none", "n/a"}:
        return False

    meta_only_markers = (
        "prefer official sources",
        "prefer using official sources",
        "prefer official eu sources",
        "when referencing compliance/impact context",
        "if relevant",
    )
    if any(marker in lowered for marker in meta_only_markers):
        factual_markers = (
            "detected",
            "observed",
            "confirmed",
            "under investigation",
            "within ",
            "check ",
            "verify ",
            "include ",
            "ask ",
            "request ",
            "report ",
            "incident",
            "customer",
            "tenant",
            "pipeline",
            "email",
        )
        if not any(marker in lowered for marker in factual_markers):
            return False

    return len(lowered) >= 8


# Recover typed external draft payloads from older checkpoints that restored nested dicts.
def coerce_external_communication_draft(
    payload: ExternalCommunicationDraft | dict[str, Any] | None,
) -> ExternalCommunicationDraft | None:
    if payload is None or isinstance(payload, ExternalCommunicationDraft):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("External communication draft payload must be a dict or dataclass.")
    return ExternalCommunicationDraft(
        channel=str(payload.get("channel") or "").strip(),
        recipient=_coerce_optional_string(payload.get("recipient")),
        subject=_coerce_optional_string(payload.get("subject")),
        body=_coerce_optional_string(payload.get("body")),
        metadata=_coerce_metadata_dict(payload.get("metadata")),
    )


# Recover typed human review external edits from older checkpoints that restored nested dicts.
def coerce_human_review_external_edit(
    payload: HumanReviewExternalEdit | dict[str, Any] | None,
) -> HumanReviewExternalEdit | None:
    if payload is None or isinstance(payload, HumanReviewExternalEdit):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("Human review external edit payload must be a dict or dataclass.")
    draft_payload = payload.get("draft", payload.get("external_communication_draft"))
    draft = coerce_external_communication_draft(draft_payload)
    if draft is None:
        raise ValueError("Human review external edit requires a draft.")
    return HumanReviewExternalEdit(
        draft=draft,
        note=_coerce_optional_string(payload.get("note")),
    )


# Normalize nested external-communication payloads after checkpoint restore.
def normalize_task_state_serialization(task_state: TaskState) -> TaskState:
    external_state = task_state.external_communication
    if external_state is not None:
        external_state.draft = coerce_external_communication_draft(external_state.draft)
        external_state.last_applied_edit = coerce_human_review_external_edit(
            external_state.last_applied_edit
        )
        external_state.edit_history = [
            edit
            for edit in (
                coerce_human_review_external_edit(edit)
                for edit in external_state.edit_history
            )
            if edit is not None
        ]

    for request_attr in ("pending_human_review_request", "last_human_review_request"):
        request = getattr(task_state, request_attr)
        if request is not None:
            setattr(
                task_state,
                request_attr,
                HumanReviewRequest(
                    request_id=request.request_id,
                    thread_id=request.thread_id,
                    current_state=request.current_state,
                    resume_state=request.resume_state,
                    current_plan_step_id=request.current_plan_step_id,
                    current_plan_step_objective=request.current_plan_step_objective,
                    actor_result_type=request.actor_result_type,
                    current_tool_request=request.current_tool_request,
                    governance_directive=request.governance_directive,
                    governance_summary=request.governance_summary,
                    static_policy_summary=request.static_policy_summary,
                    dynamic_policy_summary=request.dynamic_policy_summary,
                    behavioral_summary=request.behavioral_summary,
                    judge_summary=request.judge_summary,
                    oversight_summary=request.oversight_summary,
                    external_communication_target_type=request.external_communication_target_type,
                    external_communication_draft=coerce_external_communication_draft(
                        request.external_communication_draft
                    ),
                    reason_codes=request.reason_codes,
                    allowed_decisions=request.allowed_decisions,
                    review_summary=request.review_summary,
                ),
            )

    if task_state.last_human_review_decision is not None:
        task_state.last_human_review_decision = HumanReviewDecision(
            request_id=task_state.last_human_review_decision.request_id,
            decision_type=task_state.last_human_review_decision.decision_type,
            note=task_state.last_human_review_decision.note,
            clarification_text=task_state.last_human_review_decision.clarification_text,
            external_communication_edit=coerce_human_review_external_edit(
                task_state.last_human_review_decision.external_communication_edit
            ),
            reviewer_id=task_state.last_human_review_decision.reviewer_id,
        )

    task_state.human_review_history = [
        HumanReviewDecision(
            request_id=decision.request_id,
            decision_type=decision.decision_type,
            note=decision.note,
            clarification_text=decision.clarification_text,
            external_communication_edit=coerce_human_review_external_edit(
                decision.external_communication_edit
            ),
            reviewer_id=decision.reviewer_id,
        )
        for decision in task_state.human_review_history
    ]
    return task_state


# Keep restored optional text fields normalized to plain strings or None.
def _coerce_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# Keep restored metadata payloads JSON-like and predictable.
def _coerce_metadata_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("External communication metadata must be a dict.")
    return {str(key): item for key, item in value.items()}


__all__ = [
    "coerce_external_communication_draft",
    "coerce_human_review_external_edit",
    "ExecutedPlanStep",
    "ExternalCommunicationPhase",
    "ExternalCommunicationDraft",
    "ExternalCommunicationState",
    "HumanReviewDecision",
    "HumanReviewDecisionType",
    "HumanReviewExternalEdit",
    "HumanReviewRequest",
    "PlanStep",
    "PlanStepStatus",
    "PlannerDecision",
    "RuntimeFlag",
    "RuntimeState",
    "ScratchpadEntry",
    "ScratchpadEntryType",
    "StepRecord",
    "StepValidation",
    "StepValidationDecision",
    "TaskState",
    "normalize_task_state_serialization",
]
