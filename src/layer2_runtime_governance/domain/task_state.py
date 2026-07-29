from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from layer1_intake import (
    ExecutionIntent,
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    TaskSpec,
)

from ..tool_contract import (
    ToolExecutionResult,
    ToolObservation,
    ToolRequest,
)

if TYPE_CHECKING:
    from ..actor_contract import ActorResult
    from ..behavioral import BehavioralAssessment
    from ..governance_components.contracts import GovernanceDecisionTrace, StaticPolicyAssessment
    from ..judge import JudgeAssessment
    from ..oversight import DynamicPolicyAssessment
    from ..external_operations.contracts import (
        ExternalExecutionReceipt,
        ExternalExecutionRequest,
        ExternalOperationProposal,
        GovernanceActionFeedback,
    )


# Layer 2 currently covers bootstrap, planning, and the actor/tool observation loop.
# This file keeps the canonical runtime state shared by the graph nodes.
from .lifecycle import RuntimeFlag, RuntimeState, StepRecord
from .plan import PlanStep
from .review import (
    ExternalCommunicationPhase,
    ExternalCommunicationState,
    HumanReviewDecision,
    HumanReviewRequest,
)
from .runtime_records import (
    ExecutedPlanStep,
    ReplanCheckpoint,
    AgentWorkspace,
    ScratchpadEntry,
    StepValidation,
    StepCompletionCandidate,
    ToolAttempt,
    ToolInvocation,
)


# Canonical domain contract extracted from the legacy state facade.
@dataclass
class TaskState:
    # Direct handoff from Layer 1.
    intake_payload: IntakeResult

    # Bootstrap outcome.
    current_state: RuntimeState
    runtime_thread_id: str | None = None
    step_index: int = 0
    step_history: list[StepRecord] = field(default_factory=list)
    runtime_flags: list[RuntimeFlag] = field(default_factory=list)
    bootstrap_completed: bool = False
    human_review_resume_state: RuntimeState | None = None
    external_communication: ExternalCommunicationState | None = None
    execution_intent: ExecutionIntent = ExecutionIntent.PROPOSAL_ONLY

    # Planner snapshot.
    plan_steps: list[PlanStep] = field(default_factory=list)
    current_plan_step_index: int | None = None
    planner_rationale: str | None = None
    planner_name: str | None = None
    planner_prompt_version: str | None = None
    replan_checkpoints: list[ReplanCheckpoint] = field(default_factory=list)

    # Actor execution snapshot.
    actor_result: ActorResult | None = None
    actor_name: str | None = None
    actor_prompt_version: str | None = None
    executed_steps: list[ExecutedPlanStep] = field(default_factory=list)
    final_output_text: str | None = None
    actor_turn_count: int = 0
    # Local liveness fuse; this is technical control flow, never a policy signal.
    no_progress_turns: int = 0
    # Progress changes only when the live problem state materially changes.
    progress_epoch: int = 0
    state_revision: int = 0
    workspace: AgentWorkspace = field(default_factory=AgentWorkspace)
    # Typed runtime recovery fact; never inferred from provider error text.
    consecutive_provider_protocol_failures: int = 0
    actor_protocol_retry_count: int = 0
    actor_protocol_retry_progress_epoch: int = 0
    actor_protocol_retry_step_id: str | None = None
    actor_protocol_feedback: Any = None
    # Scratchpad is intentionally summarized working memory, safe for audit and HITL display.
    scratchpad: list[ScratchpadEntry] = field(default_factory=list)
    # Step validations are the bridge toward confidence/self-stop logic.
    step_validations: list[StepValidation] = field(default_factory=list)
    pending_completion_candidate: StepCompletionCandidate | None = None

    # Tool loop snapshot.
    current_tool_request: ToolRequest | None = None
    # Ordered actor-emitted capability requests awaiting their individual governance boundary.
    pending_tool_requests: list[ToolRequest] = field(default_factory=list)
    last_tool_result: ToolExecutionResult | None = None
    tool_results_history: list[ToolExecutionResult] = field(default_factory=list)
    observations: list[ToolObservation] = field(default_factory=list)
    tool_call_count: int = 0
    tool_attempts: list[ToolAttempt] = field(default_factory=list)
    tool_invocations: list[ToolInvocation] = field(default_factory=list)

    # Canonical external-operation snapshot derived by runtime normalizers.
    external_operation_proposal: ExternalOperationProposal | None = None
    external_operation_history: list[ExternalOperationProposal] = field(default_factory=list)
    approved_external_operation_digest: str | None = None
    governance_action_feedback: list[GovernanceActionFeedback] = field(default_factory=list)
    external_operation_revision_count: int = 0
    current_external_execution_request: ExternalExecutionRequest | None = None
    last_external_execution_receipt: ExternalExecutionReceipt | None = None
    external_execution_history: list[ExternalExecutionReceipt] = field(default_factory=list)
    external_execution_attempt_count: int = 0

    # Governance snapshot.
    static_policy_history: list[StaticPolicyAssessment] = field(default_factory=list)
    dynamic_policy_history: list[DynamicPolicyAssessment] = field(default_factory=list)
    behavioral_history: list[BehavioralAssessment] = field(default_factory=list)
    judge_history: list[JudgeAssessment] = field(default_factory=list)
    governance_history: list[GovernanceDecisionTrace] = field(default_factory=list)

    # Human-in-the-loop snapshot.
    pending_human_review_request: HumanReviewRequest | None = None
    last_human_review_request: HumanReviewRequest | None = None
    human_review_history: list[HumanReviewDecision] = field(default_factory=list)

    @property
    def task_spec(self) -> TaskSpec:
        # Derive the task contract from the canonical Layer 1 handoff.
        return self.intake_payload.structured_task.task_spec

    @property
    def execution_policy(self) -> ExecutionPolicy:
        # Derive the current execution envelope from the canonical Layer 1 handoff.
        return self.intake_payload.structured_task.execution_policy

    @execution_policy.setter
    def execution_policy(self, value: ExecutionPolicy) -> None:
        # Keep human-approved policy changes inside the canonical intake payload.
        self.intake_payload.structured_task.execution_policy = value

    @property
    def intake_metadata(self) -> IntakeMetadata:
        # Derive intake metadata instead of serializing a duplicate reference.
        return self.intake_payload.metadata

    @property
    def last_static_policy_assessment(self) -> StaticPolicyAssessment | None:
        # Expose the latest static assessment without duplicating checkpoint state.
        return self.static_policy_history[-1] if self.static_policy_history else None

    @last_static_policy_assessment.setter
    def last_static_policy_assessment(self, value: StaticPolicyAssessment | None) -> None:
        # Preserve the former assignment API for fixtures and checkpoint recovery.
        _replace_latest(self.static_policy_history, value)

    @property
    def last_dynamic_policy_assessment(self) -> DynamicPolicyAssessment | None:
        # Expose the latest dynamic assessment without a parallel oversight history.
        return self.dynamic_policy_history[-1] if self.dynamic_policy_history else None

    @last_dynamic_policy_assessment.setter
    def last_dynamic_policy_assessment(self, value: DynamicPolicyAssessment | None) -> None:
        # Preserve the former assignment API for fixtures and checkpoint recovery.
        _replace_latest(self.dynamic_policy_history, value)

    @property
    def last_oversight_assessment(self) -> DynamicPolicyAssessment | None:
        # Keep the legacy read alias without persisting the same assessment twice.
        return self.last_dynamic_policy_assessment

    @last_oversight_assessment.setter
    def last_oversight_assessment(self, value: DynamicPolicyAssessment | None) -> None:
        # Route legacy writes into the canonical dynamic history.
        self.last_dynamic_policy_assessment = value

    @property
    def oversight_history(self) -> list[DynamicPolicyAssessment]:
        # Keep the legacy history alias as a non-serialized view.
        return self.dynamic_policy_history

    @oversight_history.setter
    def oversight_history(self, value: list[DynamicPolicyAssessment]) -> None:
        # Normalize legacy checkpoint payloads onto the canonical history.
        self.dynamic_policy_history = value

    @property
    def last_behavioral_assessment(self) -> BehavioralAssessment | None:
        # Expose the latest behavioural assessment without duplicate state.
        return self.behavioral_history[-1] if self.behavioral_history else None

    @last_behavioral_assessment.setter
    def last_behavioral_assessment(self, value: BehavioralAssessment | None) -> None:
        # Preserve the former assignment API for fixtures and checkpoint recovery.
        _replace_latest(self.behavioral_history, value)

    @property
    def last_judge_assessment(self) -> JudgeAssessment | None:
        # Expose the latest judge assessment without duplicate state.
        return self.judge_history[-1] if self.judge_history else None

    @last_judge_assessment.setter
    def last_judge_assessment(self, value: JudgeAssessment | None) -> None:
        # Preserve the former assignment API for fixtures and checkpoint recovery.
        _replace_latest(self.judge_history, value)

    @property
    def last_governance_decision(self) -> GovernanceDecisionTrace | None:
        # Expose the latest OPA decision without duplicate state.
        return self.governance_history[-1] if self.governance_history else None

    @last_governance_decision.setter
    def last_governance_decision(self, value: GovernanceDecisionTrace | None) -> None:
        # Preserve the former assignment API for fixtures and checkpoint recovery.
        _replace_latest(self.governance_history, value)

    @property
    def last_human_review_decision(self) -> HumanReviewDecision | None:
        # Expose the latest operator decision from its canonical history.
        return self.human_review_history[-1] if self.human_review_history else None

    @last_human_review_decision.setter
    def last_human_review_decision(self, value: HumanReviewDecision | None) -> None:
        # Preserve the former assignment API for fixtures and checkpoint recovery.
        _replace_latest(self.human_review_history, value)

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
            current_state=current_state,
            runtime_flags=runtime_flags,
            execution_intent=(
                getattr(
                    intake_result.structured_task.task_spec,
                    "execution_intent",
                    ExecutionIntent.PROPOSAL_ONLY,
                )
                or ExecutionIntent.PROPOSAL_ONLY
            ),
            human_review_resume_state=(
                RuntimeState.PLANNING
                if current_state == RuntimeState.WAITING_HUMAN_REVIEW
                else None
            ),
        )


# Replace only the latest derived value while keeping one canonical history.
def _replace_latest(history: list[Any], value: Any | None) -> None:
    if value is None:
        history.clear()
        return
    if history:
        history[-1] = value
    else:
        history.append(value)


__all__ = ["TaskState"]
