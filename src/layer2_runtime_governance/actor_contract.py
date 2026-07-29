from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from layer1_intake import ExecutionPolicy, IntakeMetadata, TaskSpec

from .domain import (
    AgentWorkspace,
    ExternalCommunicationDraft,
    PlanStepStatus,
    RuntimeFlag,
    RuntimeState,
    ScratchpadEntry,
    StepArtifact,
    StepValidation,
    TaskState,
)
from .tool_contract import ToolExecutionResult, ToolObservation, ToolRequest
from .external_operations.contracts import (
    DatabaseStatementDraft,
    ExternalOperationProposal,
    GovernanceActionFeedback,
)


# CapabilityAccessMode keeps external-read and external-write intent explicit.


# CapabilityAccessMode keeps external-read and external-write intent explicit.
class CapabilityAccessMode(str, Enum):
    NONE = "none"
    READ_ONLY = "read_only"
    WRITE = "write"


# ActorResultType names the possible outcomes of one actor step.
class ActorResultType(str, Enum):
    PROPOSE_ACTION = "propose_action"
    INTERMEDIATE_OUTPUT = "intermediate_output"
    STEP_COMPLETED = "step_completed"
    ASK_CLARIFICATION = "ask_clarification"
    BLOCKED = "blocked"
    NEEDS_REPLAN = "needs_replan"


@dataclass(frozen=True)
class ActorProtocolFeedback:
    error_code: str
    category: str
    summary: str
    unknown_evidence_ids: tuple[str, ...] = ()
    valid_evidence_ids: tuple[str, ...] = ()
    validation_errors: tuple[str, ...] = ()
    expected_action_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class ActorPlanStep:
    # The actor receives an immutable copy of each semantic plan step.
    step_id: str
    objective: str
    rationale: str
    expected_outcome: str
    status: PlanStepStatus


@dataclass(frozen=True)
class ActorPastStep:
    # The actor receives a compact history of already executed plan steps.
    step_id: str
    objective: str
    result_type: ActorResultType
    summary: str
    output_text: str | None = None


@dataclass(frozen=True)
class ActorTask:
    # The actor sees the whole plan, past executed steps, and observed evidence.
    # Execution authority still belongs only to current_plan_step.
    task_spec: TaskSpec
    execution_policy: ExecutionPolicy
    intake_metadata: IntakeMetadata
    current_state: RuntimeState
    runtime_flags: tuple[RuntimeFlag, ...]
    plan_steps: tuple[ActorPlanStep, ...]
    past_steps: tuple[ActorPastStep, ...]
    observations: tuple[ToolObservation, ...]
    scratchpad: tuple[ScratchpadEntry, ...]
    step_validations: tuple[StepValidation, ...]
    last_tool_result: ToolExecutionResult | None
    tool_call_count: int
    remaining_tool_calls: int | None
    actor_turn_count: int
    current_plan_step_index: int
    current_plan_step: ActorPlanStep
    planner_rationale: str | None = None
    planner_name: str | None = None
    planner_prompt_version: str | None = None
    governance_feedback: tuple[GovernanceActionFeedback, ...] = ()
    workspace: AgentWorkspace | None = None
    progress_epoch: int = 0
    attempted_action_keys: tuple[str, ...] = ()
    # Set only for the one bounded local retry after a provider protocol failure.
    protocol_retry_feedback: ActorProtocolFeedback | str | None = None
    # The current runtime-owned draft, so a revision edits it instead of rewriting blindly.
    external_communication_draft: ExternalCommunicationDraft | None = None
    previous_rejected_draft: ExternalCommunicationDraft | None = None


@dataclass(frozen=True)
class ActorResult:
    # The actor returns one concrete execution outcome for the current step.
    # Runtime nodes interpret this result; the actor never mutates TaskState directly.
    plan_step_id: str
    result_type: ActorResultType
    summary: str
    capability_mode: CapabilityAccessMode = CapabilityAccessMode.NONE
    external_impact: bool = False
    output_text: str | None = None
    blocking_reason: str | None = None
    provider_protocol_failure: bool = False
    protocol_feedback: ActorProtocolFeedback | None = None
    tool_request: ToolRequest | None = None
    # A capability batch is ordered and contains no control outcomes.
    tool_requests: tuple[ToolRequest, ...] = ()
    # Optional audit-safe memory from the actor turn.
    scratchpad_entry: ScratchpadEntry | None = None
    # Required before local or final outputs can complete a plan step.
    step_validation: StepValidation | None = None
    # Providers may propose communication content; runtime still owns normalization and authority.
    external_communication_draft: ExternalCommunicationDraft | None = None
    # Database proposals carry no DSN, credentials, or connector-selected tool name.
    database_statement_draft: DatabaseStatementDraft | None = None
    # Runtime normalizers populate this field; provider-supplied boundary flags are not authoritative.
    external_operation_proposal: ExternalOperationProposal | None = None
    # An actor can persist a compact durable delta without turning audit prose into progress.
    artifact_delta: StepArtifact | None = None


class ActorPort(Protocol):
    # The actor port keeps runtime wiring decoupled from the concrete implementation.
    actor_name: str
    prompt_version: str

    def act(self, *, actor_task: ActorTask) -> ActorResult: ...


# Build the immutable planner-to-actor handoff from the current runtime state.
def build_actor_task(*, task_state: TaskState) -> ActorTask:
    # The actor contract is valid only when the planner has selected one executable step.
    if task_state.current_plan_step_index is None:
        raise ValueError("Cannot build ActorTask without current_plan_step_index.")

    current_index = task_state.current_plan_step_index
    if not 0 <= current_index < len(task_state.plan_steps):
        raise ValueError("current_plan_step_index is out of bounds for plan_steps.")

    if task_state.current_plan_step is None:
        raise ValueError("Cannot build ActorTask without a current plan step.")

    # Freeze the semantic plan so the actor can inspect it without mutating runtime state.
    actor_plan_steps = tuple(
        ActorPlanStep(
            step_id=step.step_id,
            objective=step.objective,
            rationale=step.rationale,
            expected_outcome=step.expected_outcome,
            status=step.status,
        )
        for step in task_state.plan_steps
    )

    # Freeze the executed step history so the actor can reason from prior completions.
    actor_past_steps = tuple(
        ActorPastStep(
            step_id=step.step_id,
            objective=step.objective,
            result_type=step.result_type,
            summary=step.summary,
            output_text=step.output_text,
        )
        for step in task_state.executed_steps
    )

    return ActorTask(
        task_spec=task_state.task_spec.model_copy(deep=True),
        execution_policy=task_state.execution_policy.model_copy(deep=True),
        intake_metadata=task_state.intake_metadata.model_copy(deep=True),
        current_state=task_state.current_state,
        runtime_flags=tuple(task_state.runtime_flags),
        plan_steps=actor_plan_steps,
        past_steps=actor_past_steps,
        observations=tuple(task_state.observations),
        scratchpad=tuple(task_state.scratchpad),
        step_validations=tuple(task_state.step_validations),
        last_tool_result=task_state.last_tool_result,
        tool_call_count=task_state.tool_call_count,
        # Keep tool budget explicit while allowing policies with no hard tool-call ceiling.
        remaining_tool_calls=_compute_remaining_tool_calls(task_state=task_state),
        actor_turn_count=task_state.actor_turn_count,
        current_plan_step_index=current_index,
        current_plan_step=actor_plan_steps[current_index],
        planner_rationale=task_state.planner_rationale,
        planner_name=task_state.planner_name,
        planner_prompt_version=task_state.planner_prompt_version,
        governance_feedback=tuple(task_state.governance_action_feedback),
        workspace=task_state.workspace,
        progress_epoch=task_state.progress_epoch,
        attempted_action_keys=tuple(item.action_key for item in task_state.tool_attempts),
        external_communication_draft=(
            task_state.external_communication.draft
            if task_state.external_communication is not None
            else None
        ),
        protocol_retry_feedback=task_state.actor_protocol_feedback,
        previous_rejected_draft=(
            task_state.external_communication.draft
            if (
                task_state.external_communication is not None
                and task_state.governance_action_feedback
            )
            else None
        ),
    )


# Convert the optional tool-call budget into a remaining count the actor can reason about.
def _compute_remaining_tool_calls(*, task_state: TaskState) -> int | None:
    max_tool_calls = task_state.execution_policy.budget.max_tool_calls
    if max_tool_calls is None:
        return None

    return max(max_tool_calls - task_state.tool_call_count, 0)


__all__ = [
    "ActorPastStep",
    "ActorPlanStep",
    "ActorPort",
    "ActorProtocolFeedback",
    "ActorResult",
    "ActorResultType",
    "ActorTask",
    "CapabilityAccessMode",
    "ScratchpadEntry",
    "ScratchpadEntryType",
    "StepValidation",
    "build_actor_task",
]
