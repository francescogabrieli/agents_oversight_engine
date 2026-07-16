from __future__ import annotations

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

from ..tool_contract import (
    EvidenceSupportLevel,
    ObservedEvidence,
    ToolExecutionResult,
    ToolRequest,
)

if TYPE_CHECKING:
    from .actor_contract import ActorResult, ActorResultType
    from .behavioral import BehavioralAssessment
    from ..governance_components.contracts import GovernanceDecisionTrace, StaticPolicyAssessment
    from .judge import JudgeAssessment
    from .oversight import DynamicPolicyAssessment, OversightAssessment


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
from .runtime_records import ExecutedPlanStep, ScratchpadEntry, StepValidation


# Canonical domain contract extracted from the legacy state facade.
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


# Canonical domain contract extracted from the legacy state facade.
def _build_initial_observed_evidence_from_intake(task_spec: TaskSpec) -> list[ObservedEvidence]:
    # Keep TaskState construction stable while intake evidence owns its own rules.
    from .intake_evidence import build_initial_observed_evidence

    return build_initial_observed_evidence(task_spec)


__all__ = [
    "TaskState",
    "_build_initial_observed_evidence_from_intake"
]
