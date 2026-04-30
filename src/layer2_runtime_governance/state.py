from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from layer1_intake import (
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    TaskSpec,
)

from .tool_contract import ObservedEvidence, ToolExecutionResult, ToolRequest

if TYPE_CHECKING:
    from .actor_contract import ActorResult, ActorResultType
    from .governance import GovernanceDecision
    from .oversight import OversightAssessment


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


@dataclass
class TaskState:
    # Direct handoff from Layer 1.
    intake_payload: IntakeResult
    task_spec: TaskSpec
    execution_policy: ExecutionPolicy
    intake_metadata: IntakeMetadata

    # Bootstrap outcome.
    current_state: RuntimeState
    step_index: int = 0
    step_history: list[StepRecord] = field(default_factory=list)
    runtime_flags: list[RuntimeFlag] = field(default_factory=list)
    bootstrap_completed: bool = False

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
    last_governance_decision: GovernanceDecision | None = None
    governance_history: list[GovernanceDecision] = field(default_factory=list)
    last_oversight_assessment: OversightAssessment | None = None
    oversight_history: list[OversightAssessment] = field(default_factory=list)

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
        )


__all__ = [
    "ExecutedPlanStep",
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
]
