from dataclasses import replace

from ..actor_contract import ActorPort, ActorResult, ActorResultType, build_actor_task
from ..domain import (
    ExecutedPlanStep,
    ExternalCommunicationDraft,
    ExternalCommunicationPhase,
    ExternalCommunicationState,
    PlanStepStatus,
    RuntimeState,
    ScratchpadEntry,
    ScratchpadEntryType,
    StepArtifact,
    StepCompletionCandidate,
    TaskState,
)

# Nodes.py contains the executable Layer 2 business logic.
# At this stage the runtime performs bootstrap, planning, and the actor/tool loop.
from .lifecycle_service import _append_step_record
from .completion_contract import terminal_actor_completion_omits_external_candidate

# Apply actor progress while preserving typed runtime invariants.
def actor(state: TaskState, *, actor_service: ActorPort) -> TaskState:
    if state.current_state not in {
        RuntimeState.PLANNING,
        RuntimeState.ACTING,
        RuntimeState.READY_NEXT_STEP,
    } or state.current_plan_step is None:
        _append_step_record(
            state,
            node_name="actor",
            summary="Actor skipped because there is no executable plan step in an actor-ready state.",
        )
        return state

    state.actor_turn_count += 1
    state.no_progress_turns += 1

    actor_task = build_actor_task(task_state=state)
    actor_result = actor_service.act(actor_task=actor_task)
    actor_result = _enforce_actor_runtime_contract(
        state,
        actor_result=actor_result,
    )

    # A successful actor turn creates a new action candidate. The previous immutable
    # proposal remains in audit history but cannot govern or authorize the new result.
    state.external_operation_proposal = actor_result.external_operation_proposal
    state.approved_external_operation_digest = None
    state.current_external_execution_request = None
    state.actor_result = actor_result
    if actor_result.provider_protocol_failure:
        state.consecutive_provider_protocol_failures += 1
    else:
        state.consecutive_provider_protocol_failures = 0
    state.actor_name = actor_service.actor_name
    state.actor_prompt_version = actor_service.prompt_version
    state.current_tool_request = (
        actor_result.tool_request if actor_result.result_type == ActorResultType.PROPOSE_ACTION else None
    )

    _record_actor_execution(state, actor_result=actor_result)
    state.current_state = _map_actor_result_to_runtime_state(state, actor_result=actor_result)

    _append_step_record(
        state,
        node_name="actor",
        summary="Actor executed the current semantic step and returned one typed execution outcome.",
    )
    return state


# Convert semantic protocol violations into explicit recovery instead of false progress.
def _enforce_actor_runtime_contract(
    state: TaskState,
    *,
    actor_result: ActorResult,
) -> ActorResult:
    if state.no_progress_turns > 3 and not _has_changed_action_basis(state):
        return _build_runtime_replan_result(
            state,
            actor_result=actor_result,
            summary="Technical liveness fuse requested replanning.",
            blocking_reason=(
                "The current trajectory produced four actor turns without a concrete observation, "
                "durable artifact, completed step, human decision, or changed semantic plan."
            ),
        )
    if actor_result.provider_protocol_failure and state.consecutive_provider_protocol_failures >= 1:
        return _build_runtime_replan_result(
            state,
            actor_result=actor_result,
            summary="Provider recovery exhausted its one local correction.",
            blocking_reason=(
                "The provider produced a second unusable runtime action on unchanged state; "
                "local protocol recovery is exhausted."
            ),
        )
    if _repeats_identical_intermediate_output(state, actor_result=actor_result):
        return _build_runtime_replan_result(
            state,
            actor_result=actor_result,
            summary="Actor repeated an identical intermediate update.",
            blocking_reason=(
                "The current semantic step repeated the same audit-safe intermediate "
                "output without adding a new observation, decision, uncertainty, or action."
            ),
        )
    if actor_result.result_type == ActorResultType.INTERMEDIATE_OUTPUT:
        return _build_runtime_replan_result(
            state,
            actor_result=actor_result,
            summary="Legacy narrative actor output has no operational decision.",
            blocking_reason=(
                "The provider emitted legacy intermediate narration without a capability request, "
                "durable artifact delta, completion, clarification, or semantic replan basis."
            ),
        )
    if (
        actor_result.result_type == ActorResultType.STEP_COMPLETED
        and terminal_actor_completion_omits_external_candidate(
            state,
            actor_result=actor_result,
        )
    ):
        return _build_runtime_replan_result(
            state,
            actor_result=actor_result,
            summary="Terminal completion omitted the requested external operation.",
            blocking_reason=(
                "The task requires governed external execution, but the terminal actor "
                "outcome contained no typed external-operation candidate. The remaining "
                "trajectory must produce the requested candidate before completion."
            ),
        )

    return actor_result


# Distinct bounded capability attempts are observable trajectory changes even when all fail.
def _has_changed_action_basis(state: TaskState) -> bool:
    action_keys = {
        (
            item.request.tool_name.value,
            item.request.query,
            item.request.url,
            item.request.path,
            item.request.expression,
        )
        for item in state.tool_results_history
    }
    return len(action_keys) > 1


# Treat exact replay as a stuck actor protocol, not as useful multi-turn reasoning.
def _repeats_identical_intermediate_output(
    state: TaskState,
    *,
    actor_result: ActorResult,
) -> bool:
    previous = state.actor_result
    if (
        previous is None
        or previous.result_type != ActorResultType.INTERMEDIATE_OUTPUT
        or actor_result.result_type != ActorResultType.INTERMEDIATE_OUTPUT
        or previous.plan_step_id != actor_result.plan_step_id
    ):
        return False

    previous_note = previous.scratchpad_entry
    current_note = actor_result.scratchpad_entry
    return (
        _normalized_actor_text(previous.summary)
        == _normalized_actor_text(actor_result.summary)
        and _normalized_actor_text(previous.output_text)
        == _normalized_actor_text(actor_result.output_text)
        and _normalized_actor_text(
            previous_note.summary if previous_note is not None else None
        )
        == _normalized_actor_text(
            current_note.summary if current_note is not None else None
        )
    )


# Normalize whitespace and casing only for exact semantic replay detection.
def _normalized_actor_text(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


# Preserve the actor turn in the audit trail while routing its failed contract to replanning.
def _build_runtime_replan_result(
    state: TaskState,
    *,
    actor_result: ActorResult,
    summary: str,
    blocking_reason: str,
) -> ActorResult:
    return replace(
        actor_result,
        result_type=ActorResultType.NEEDS_REPLAN,
        summary=summary,
        capability_mode=actor_result.capability_mode,
        external_impact=False,
        output_text=None,
        blocking_reason=blocking_reason,
        tool_request=None,
        scratchpad_entry=ScratchpadEntry(
            turn_index=state.actor_turn_count,
            plan_step_id=actor_result.plan_step_id,
            entry_type=ScratchpadEntryType.UNCERTAINTY,
            summary=blocking_reason,
        ),
        # Preserve the provider's audit-safe validation record; it is not evidence or progress.
        step_validation=actor_result.step_validation,
        external_communication_draft=None,
        database_statement_draft=None,
        external_operation_proposal=None,
    )

# Apply actor progress while preserving typed runtime invariants.
def _record_actor_execution(state: TaskState, *, actor_result: ActorResult) -> None:
    current_step = state.current_plan_step
    if current_step is None:
        raise ValueError("Cannot record actor execution without a current plan step.")

    if actor_result.plan_step_id != current_step.step_id:
        raise ValueError("actor_result.plan_step_id does not match the current plan step.")

    _update_external_communication_after_actor_result(state, actor_result=actor_result)

    if actor_result.scratchpad_entry is not None:
        # Store summarized working memory before any routing decision changes the state.
        state.scratchpad.append(actor_result.scratchpad_entry)

    if actor_result.artifact_delta is not None:
        _record_workspace_artifact(state, artifact=actor_result.artifact_delta)

    if actor_result.step_validation is not None:
        if actor_result.step_validation.plan_step_id != current_step.step_id:
            raise ValueError("step_validation.plan_step_id does not match the current plan step.")
        state.step_validations.append(actor_result.step_validation)

    if actor_result.result_type in {
        ActorResultType.ASK_CLARIFICATION,
        ActorResultType.BLOCKED,
        ActorResultType.NEEDS_REPLAN,
    }:
        current_step.status = PlanStepStatus.BLOCKED
        state.current_tool_request = None
        return

    if actor_result.result_type == ActorResultType.PROPOSE_ACTION:
        if actor_result.tool_request is None:
            raise ValueError("propose_action requires tool_request.")
        # Tool proposals keep the current semantic step alive until observation returns.
        current_step.status = PlanStepStatus.IN_PROGRESS
        return

    if actor_result.result_type == ActorResultType.INTERMEDIATE_OUTPUT:
        # Progress output deliberately leaves the semantic step open.
        current_step.status = PlanStepStatus.IN_PROGRESS
        return

    if actor_result.result_type != ActorResultType.STEP_COMPLETED:
        current_step.status = PlanStepStatus.IN_PROGRESS
        return

    materialized_output = actor_result.output_text
    if (
        (not materialized_output or not materialized_output.strip())
        and actor_result.external_operation_proposal is not None
    ):
        # The immutable normalized proposal is the materialized result for an external candidate.
        materialized_output = actor_result.external_operation_proposal.payload_preview
    if not materialized_output or not materialized_output.strip():
        raise ValueError("step_completed requires materialized output_text.")
    # Candidate creation keeps the semantic cursor stable until the governing boundary accepts it.
    state.pending_completion_candidate = StepCompletionCandidate(
        candidate_id=f"{current_step.step_id}:candidate:{state.actor_turn_count}",
        step_id=current_step.step_id,
        summary=actor_result.summary,
        output_text=materialized_output.strip(),
        evidence_refs=(actor_result.step_validation.evidence_refs if actor_result.step_validation else ()),
        external_operation_id=(actor_result.external_operation_proposal.operation_id if actor_result.external_operation_proposal else None),
        external_operation_digest=(actor_result.external_operation_proposal.payload_digest if actor_result.external_operation_proposal else None),
    )
    current_step.status = PlanStepStatus.IN_PROGRESS
    state.current_tool_request = None


# Persist only changed durable artifacts and advance the local progress epoch.
def _record_workspace_artifact(state: TaskState, *, artifact: StepArtifact) -> None:
    previous = next(
        (item for item in state.workspace.artifacts if item.artifact_id == artifact.artifact_id),
        None,
    )
    if previous is not None and previous.content == artifact.content and previous.evidence_refs == artifact.evidence_refs:
        return
    state.workspace.artifacts = [
        item for item in state.workspace.artifacts if item.artifact_id != artifact.artifact_id
    ] + [artifact]
    state.workspace.revision += 1
    state.progress_epoch += 1
    state.state_revision += 1
    state.no_progress_turns = 0


# Commit a previously accepted candidate atomically; rejected candidates never enter history.
def commit_completion_candidate(state: TaskState) -> None:
    candidate = state.pending_completion_candidate
    current_step = state.current_plan_step
    if candidate is None or current_step is None or candidate.step_id != current_step.step_id:
        raise ValueError("No completion candidate matches the current plan step.")
    current_step.status = PlanStepStatus.COMPLETED
    _record_workspace_artifact(
        state,
        artifact=StepArtifact(
            artifact_id=f"{candidate.step_id}:completed:{len(state.executed_steps) + 1}",
            step_id=candidate.step_id,
            content=candidate.output_text,
            evidence_refs=candidate.evidence_refs,
            provenance="accepted_completion",
        ),
    )
    state.executed_steps.append(ExecutedPlanStep(candidate.step_id, current_step.objective, ActorResultType.STEP_COMPLETED, candidate.summary, candidate.output_text))
    state.pending_completion_candidate = None
    state.last_tool_result = None
    _advance_to_next_plan_step(state)
    if state.current_plan_step_index is None:
        state.final_output_text = _build_external_communication_draft_body(state) if state.external_communication is not None else candidate.output_text

# Apply actor progress while preserving typed runtime invariants.
def _update_external_communication_after_actor_result(
    state: TaskState,
    *,
    actor_result: ActorResult,
) -> None:
    if (
        actor_result.external_communication_draft is not None
        and state.external_communication is None
    ):
        state.external_communication = ExternalCommunicationState(
            phase=ExternalCommunicationPhase.DRAFT_IN_PROGRESS,
            last_transition_summary=(
                "The actor produced a typed external communication draft."
            ),
        )
    if state.external_communication is None:
        return

    if actor_result.result_type in {
        ActorResultType.ASK_CLARIFICATION,
        ActorResultType.NEEDS_REPLAN,
    }:
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.EVIDENCE_COLLECTION,
            summary="The external communication branch needs more evidence or a revised plan before drafting.",
            review_ready=False,
        )
        return

    if actor_result.result_type == ActorResultType.BLOCKED:
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.EVIDENCE_COLLECTION,
            summary="The external communication branch hit a recoverable runtime block and remains in recovery.",
            review_ready=False,
        )
        return

    if (
        state.external_communication is not None
        and state.external_communication.phase == ExternalCommunicationPhase.APPROVED_FOR_EXECUTION
    ):
        return

    if actor_result.output_text and actor_result.output_text.strip():
        review_ready = _external_communication_output_is_review_ready(
            state,
            actor_result=actor_result,
        )
        _merge_external_communication_draft(
            state,
            output_text=actor_result.output_text.strip(),
            typed_draft=actor_result.external_communication_draft,
            review_ready=review_ready,
        )
        _set_external_communication_phase(
            state,
            phase=(
                ExternalCommunicationPhase.DRAFT_PROPOSAL
                if review_ready
                else ExternalCommunicationPhase.DRAFT_IN_PROGRESS
            ),
            summary=(
                "A review-ready external draft proposal exists and remains inside the runtime boundary."
                if review_ready
                else "A partial external draft is being assembled inside the runtime boundary."
            ),
            review_ready=review_ready,
        )

# Apply actor progress while preserving typed runtime invariants.
def _set_external_communication_phase(
    state: TaskState,
    *,
    phase: ExternalCommunicationPhase,
    summary: str,
    review_ready: bool | None = None,
) -> None:
    if state.external_communication is None:
        return
    state.external_communication.phase = phase
    if review_ready is not None:
        state.external_communication.review_ready = review_ready
    state.external_communication.last_transition_summary = summary

# Apply actor progress while preserving typed runtime invariants.
def _external_communication_output_is_review_ready(
    state: TaskState,
    *,
    actor_result: ActorResult,
) -> bool:
    current_step = state.current_plan_step
    if current_step is None:
        return False

    if actor_result.result_type not in {
        ActorResultType.STEP_COMPLETED,
    }:
        return False

    return actor_result.external_communication_draft is not None

# Apply actor progress while preserving typed runtime invariants.
def _merge_external_communication_draft(
    state: TaskState,
    *,
    output_text: str,
    typed_draft: ExternalCommunicationDraft | None = None,
    review_ready: bool,
) -> None:
    if state.external_communication is None:
        return

    current_draft = state.external_communication.draft
    metadata = dict(current_draft.metadata) if current_draft is not None else {}
    if typed_draft is not None:
        metadata.update(typed_draft.metadata)
    if "source" not in metadata:
        metadata["source"] = "actor_output"
    metadata.setdefault("content_format", "markdown")
    metadata["review_ready"] = review_ready

    state.external_communication.draft = ExternalCommunicationDraft(
        channel=(
            typed_draft.channel
            if typed_draft is not None
            else current_draft.channel
            if current_draft is not None
            else "external_communication"
        ),
        recipient=(
            typed_draft.recipient
            if typed_draft is not None
            else current_draft.recipient
            if current_draft is not None
            else None
        ),
        subject=(
            typed_draft.subject
            if typed_draft is not None
            else current_draft.subject
            if current_draft is not None
            else None
        ),
        body=_build_external_communication_draft_body(
            state,
            current_output_text=output_text,
        ),
        cc=(
            typed_draft.cc
            if typed_draft is not None
            else current_draft.cc
            if current_draft is not None
            else ()
        ),
        bcc=(
            typed_draft.bcc
            if typed_draft is not None
            else current_draft.bcc
            if current_draft is not None
            else ()
        ),
        metadata=metadata,
    )

# Apply actor progress while preserving typed runtime invariants.
def _build_external_communication_draft_body(
    state: TaskState,
    *,
    current_output_text: str | None = None,
) -> str | None:
    current_fragment = (
        current_output_text.strip()
        if current_output_text is not None and current_output_text.strip()
        else None
    )
    if current_fragment is not None:
        return current_fragment

    for step in reversed(state.executed_steps):
        if step.output_text and step.output_text.strip():
            return step.output_text.strip()

    if state.external_communication is not None and state.external_communication.draft is not None:
        draft_body = state.external_communication.draft.body
        if draft_body and draft_body.strip():
            return draft_body.strip()

    if not state.executed_steps:
        return None
    return None

# Apply actor progress while preserving typed runtime invariants.
def _advance_to_next_plan_step(state: TaskState) -> None:
    current_index = state.current_plan_step_index
    if current_index is None:
        return

    for next_index in range(current_index + 1, len(state.plan_steps)):
        next_step = state.plan_steps[next_index]
        if next_step.status == PlanStepStatus.PENDING:
            next_step.status = PlanStepStatus.IN_PROGRESS
            state.current_plan_step_index = next_index
            return

    state.current_plan_step_index = None

# Apply actor progress while preserving typed runtime invariants.
def _map_actor_result_to_runtime_state(
    state: TaskState,
    *,
    actor_result: ActorResult,
) -> RuntimeState:
    if actor_result.result_type == ActorResultType.NEEDS_REPLAN:
        return RuntimeState.REPLANNING

    if actor_result.result_type in {
        ActorResultType.ASK_CLARIFICATION,
    }:
        return RuntimeState.WAITING_HUMAN_REVIEW

    if actor_result.result_type == ActorResultType.BLOCKED:
        return RuntimeState.ACTING

    if actor_result.result_type == ActorResultType.PROPOSE_ACTION:
        return RuntimeState.READY_TOOL_EXECUTION

    if actor_result.result_type == ActorResultType.INTERMEDIATE_OUTPUT:
        return RuntimeState.ACTING

    if actor_result.result_type == ActorResultType.STEP_COMPLETED:
        return RuntimeState.READY_GOVERNANCE

    if state.current_plan_step_index is None:
        return RuntimeState.COMPLETED

    return RuntimeState.READY_NEXT_STEP

__all__ = [
    "actor",
    "_record_actor_execution",
    "_update_external_communication_after_actor_result",
    "_set_external_communication_phase",
    "_external_communication_output_is_review_ready",
    "_merge_external_communication_draft",
    "_build_external_communication_draft_body",
    "_advance_to_next_plan_step",
    "_map_actor_result_to_runtime_state",
    "_enforce_actor_runtime_contract",
    "_repeats_identical_intermediate_output",
    "_record_workspace_artifact",
    "commit_completion_candidate",
]
