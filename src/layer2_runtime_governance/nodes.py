from __future__ import annotations

from copy import deepcopy
from typing import Callable

from layer1_intake import DomainType, IntakeResult

from .behavioral import (
    BehavioralStorePort,
    build_behavioral_assessment,
    persist_behavioral_snapshot,
    persist_judge_snapshot,
)
from .actor_contract import ActorPort, ActorResult, ActorResultType, build_actor_task
from .governance import (
    GovernanceDecision,
    GovernanceDirective,
    PolicyEnginePort,
    PolicyEvaluationInput,
    combine_governance_assessments,
    build_governance_decision,
    build_policy_evaluation_input,
    evaluate_static_policy,
)
from .judge import JudgePort, evaluate_judge
from .oversight import evaluate_dynamic_policy
from .planning import PlannerPort
from .hitl import build_human_review_request
from .hitl import human_review
from .state import (
    ExecutedPlanStep,
    ExternalCommunicationDraft,
    ExternalCommunicationPhase,
    PlanStep,
    PlanStepStatus,
    RuntimeFlag,
    RuntimeState,
    ScratchpadEntry,
    ScratchpadEntryType,
    StepRecord,
    StepValidationDecision,
    TaskState,
)
from .tool_contract import (
    EvidenceSupportLevel,
    ObservedEvidence,
    ToolExecutionResult,
    ToolGatewayPort,
    ToolName,
    ToolRequest,
)
from .replanning import ReplannerPort


# Nodes.py contains the executable Layer 2 business logic.
# At this stage the runtime performs bootstrap, planning, and the actor/tool loop.
def bootstrap_from_intake(intake_result: IntakeResult) -> TaskState:
    # Build the first TaskState and record that the bootstrap node executed.
    state = TaskState.from_intake(intake_result)
    state.step_history.append(
        StepRecord(
            step_index=0,
            runtime_state=state.current_state,
            node_name="bootstrap_from_intake",
            summary="Runtime state initialized from layer 1 intake payload.",
        )
    )
    return state


# Build the initial semantic full plan unless the task already requires human clarification.
def planner(state: TaskState, *, planner_service: PlannerPort) -> TaskState:
    if state.current_state != RuntimeState.PLANNING:
        _append_step_record(
            state,
            node_name="planner",
            summary="Planning skipped because the runtime is not currently in a planning state.",
        )
        return state

    decision = planner_service.plan(task_state=state)
    planned_steps = _compact_small_source_summary_plan(
        task_state=state,
        plan_steps=decision.plan_steps,
    )
    state.plan_steps = _enforce_plan_step_budget(
        plan_steps=planned_steps,
        max_steps=state.execution_policy.budget.max_steps,
    )
    state.current_plan_step_index = 0 if state.plan_steps else None
    state.planner_rationale = decision.rationale
    state.planner_name = decision.planner_name
    state.planner_prompt_version = decision.prompt_version

    if state.current_plan_step is not None:
        state.current_plan_step.status = PlanStepStatus.IN_PROGRESS

    planner_summary = "Initial semantic full plan created from the current task state."
    if len(planned_steps) != len(decision.plan_steps):
        planner_summary = "Initial semantic full plan created from the current task state and normalized to the task scope."
    elif len(state.plan_steps) != len(planned_steps):
        planner_summary = "Initial semantic full plan created from the current task state and trimmed to the configured semantic step budget."

    _append_step_record(
        state,
        node_name="planner",
        summary=planner_summary,
    )
    return state


# Ask the actor to execute the current semantic plan step or continue its local tool loop.
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

    actor_task = build_actor_task(task_state=state)
    actor_result = actor_service.act(actor_task=actor_task)

    state.actor_result = actor_result
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


# Route the actor outcome through the deterministic governance layer.
def governance(
    state: TaskState,
    *,
    policy_engine_service: PolicyEnginePort,
    judge_service: JudgePort | None = None,
    behavioral_store_service: BehavioralStorePort | None = None,
    thread_id: str | None = None,
    hitl_event_callback: Callable[[TaskState], None] | None = None,
) -> TaskState:
    if state.actor_result is None:
        _append_step_record(
            state,
            node_name="governance",
            summary="Governance skipped because there was no actor result to evaluate.",
        )
        return state

    policy_input = build_policy_evaluation_input(task_state=state)
    static_policy_assessment = evaluate_static_policy(
        policy_engine_service=policy_engine_service,
        policy_input=policy_input,
    )
    dynamic_policy_assessment = evaluate_dynamic_policy(
        task_state=state,
        policy_input=policy_input,
    )
    resolved_thread_id = thread_id or state.runtime_thread_id or state.intake_metadata.request_id
    behavioral_assessment = build_behavioral_assessment(
        thread_id=resolved_thread_id,
        task_state=state,
        dynamic_policy_assessment=dynamic_policy_assessment,
        behavioral_store=behavioral_store_service,
    )
    judge_assessment = evaluate_judge(
        task_state=state,
        policy_input=policy_input,
        static_policy_assessment=static_policy_assessment,
        dynamic_policy_assessment=dynamic_policy_assessment,
        behavioral_assessment=behavioral_assessment,
        judge_service=judge_service,
    )
    governance_decision = combine_governance_assessments(
        policy_input=policy_input,
        static_policy_assessment=static_policy_assessment,
        dynamic_policy_assessment=dynamic_policy_assessment,
        behavioral_assessment=behavioral_assessment,
        judge_assessment=judge_assessment,
    )

    state.last_static_policy_assessment = static_policy_assessment
    state.static_policy_history.append(static_policy_assessment)
    state.last_dynamic_policy_assessment = dynamic_policy_assessment
    state.dynamic_policy_history.append(dynamic_policy_assessment)
    state.last_behavioral_assessment = behavioral_assessment
    state.behavioral_history.append(behavioral_assessment)
    state.last_judge_assessment = judge_assessment
    state.judge_history.append(judge_assessment)
    state.last_oversight_assessment = dynamic_policy_assessment
    state.oversight_history.append(dynamic_policy_assessment)
    state.last_governance_decision = governance_decision
    state.governance_history.append(governance_decision)
    _apply_governance_decision(
        state,
        governance_decision=governance_decision,
        policy_input=policy_input,
    )

    if state.current_state == RuntimeState.WAITING_HUMAN_REVIEW:
        state.pending_human_review_request = build_human_review_request(
            state=state,
            thread_id=resolved_thread_id,
        )
        if hitl_event_callback is not None:
            # Emit the HITL event before the graph pauses so the control plane can surface it.
            hitl_event_callback(deepcopy(state))
    else:
        state.pending_human_review_request = None
        state.human_review_resume_state = None

    # Persist the behavioural read model after each governance pass without affecting the decision path.
    try:
        persist_behavioral_snapshot(
            thread_id=resolved_thread_id,
            intake_result=state.intake_payload,
            task_state=state,
            behavioral_store=behavioral_store_service,
        )
    except Exception:
        pass
    try:
        persist_judge_snapshot(
            thread_id=resolved_thread_id,
            task_state=state,
            behavioral_store=behavioral_store_service,
        )
    except Exception:
        pass

    _append_step_record(
        state,
        node_name="governance",
        summary=(
            "Governance evaluated the actor outcome and produced "
            f"directive '{governance_decision.directive.value}'."
        ),
    )
    return state


# Execute the current typed tool request through the external tool gateway.
def tool_gateway(state: TaskState, *, tool_gateway_service: ToolGatewayPort) -> TaskState:
    if state.current_state != RuntimeState.READY_TOOL_EXECUTION or state.current_tool_request is None:
        _append_step_record(
            state,
            node_name="tool_gateway",
            summary="Tool gateway skipped because no tool request was ready for execution.",
        )
        return state

    # Convert budget exhaustion into a typed failed tool result instead of crashing the loop.
    if _tool_call_budget_exhausted(state=state):
        state.last_tool_result = ToolExecutionResult(
            request=state.current_tool_request,
            success=False,
            summary="Tool call budget exhausted before executing the requested tool.",
            error_message="The runtime exhausted the configured tool call budget.",
        )
        state.tool_results_history.append(state.last_tool_result)
        state.current_tool_request = None
        state.current_state = RuntimeState.ACTING

        _append_step_record(
            state,
            node_name="tool_gateway",
            summary="Tool gateway did not execute the request because the tool budget was exhausted.",
        )
        return state

    duplicate_error = _duplicate_tool_request_error(state=state)
    if duplicate_error is not None:
        state.last_tool_result = ToolExecutionResult(
            request=state.current_tool_request,
            success=False,
            summary="Duplicate tool request blocked before execution.",
            error_message=duplicate_error,
        )
        state.tool_results_history.append(state.last_tool_result)
        state.current_tool_request = None
        state.current_state = RuntimeState.ACTING

        _append_step_record(
            state,
            node_name="tool_gateway",
            summary="Tool gateway blocked a duplicate request for the current plan step.",
        )
        return state

    tool_result = tool_gateway_service.execute(tool_request=state.current_tool_request)
    state.last_tool_result = tool_result
    state.tool_results_history.append(tool_result)
    state.tool_call_count += 1
    state.current_tool_request = None
    state.current_state = RuntimeState.ACTING

    _append_step_record(
        state,
        node_name="tool_gateway",
        summary=f"Tool gateway executed '{tool_result.request.tool_name.value}'.",
    )
    return state


# Observe the latest tool result and convert it into explicit evidence for the actor.
def observe_tool_result(state: TaskState) -> TaskState:
    if state.last_tool_result is None:
        _append_step_record(
            state,
            node_name="observe_tool_result",
            summary="Observation skipped because there was no tool result to observe.",
        )
        return state

    # Observation should preserve the failed result too, so the actor can react to it explicitly.
    new_evidence = _build_observed_evidence_from_tool_result(
        state=state,
        tool_result=state.last_tool_result,
    )
    state.observed_evidence.extend(new_evidence)

    # Promote repeated empty URL extraction failures into an explicit runtime flag.
    if _tool_result_indicates_extractability_limit(state.last_tool_result):
        if RuntimeFlag.EXTRACTABILITY_LIMITED not in state.runtime_flags:
            state.runtime_flags.append(RuntimeFlag.EXTRACTABILITY_LIMITED)
    state.current_state = RuntimeState.ACTING

    _append_step_record(
        state,
        node_name="observe_tool_result",
        summary="Tool result observed and converted into explicit runtime evidence.",
    )
    return state


def finalize_bootstrap(state: TaskState) -> TaskState:
    # Close the current runtime slice after the planner/actor/tool loop reaches a stable stop point.
    next_state = deepcopy(state)
    next_state.bootstrap_completed = True
    next_state.step_index += 1
    next_state.step_history.append(
        StepRecord(
            step_index=next_state.step_index,
            runtime_state=next_state.current_state,
            node_name="finalize_bootstrap",
            summary=_build_finalize_summary(next_state),
        )
    )
    return next_state


# Replace the current/pending trajectory after the actor requests replanning.
def replanner(state: TaskState, *, replanner_service: ReplannerPort) -> TaskState:
    if state.current_state != RuntimeState.REPLANNING:
        _append_step_record(
            state,
            node_name="replanner",
            summary="Replanner skipped because the runtime is not in replanning state.",
        )
        return state

    if state.current_plan_step_index is None:
        _append_step_record(
            state,
            node_name="replanner",
            summary="Replanner skipped because there is no current plan step to replace.",
        )
        return state

    decision = replanner_service.replan(task_state=state)
    # Completed steps are immutable history; only current and future trajectory is replaced.
    completed_steps = [
        step for step in state.plan_steps if step.status == PlanStepStatus.COMPLETED
    ]

    replacement_steps = list(decision.replacement_steps)
    if replacement_steps:
        # The first replacement step becomes the next executable actor target.
        replacement_steps[0].status = PlanStepStatus.IN_PROGRESS
        for step in replacement_steps[1:]:
            step.status = PlanStepStatus.PENDING

    state.plan_steps = completed_steps + replacement_steps
    state.current_plan_step_index = len(completed_steps) if replacement_steps else None
    state.planner_rationale = decision.rationale
    state.planner_name = decision.replanner_name
    state.planner_prompt_version = decision.prompt_version
    # Replanning replaces the live trajectory, so stale tool execution context must not leak forward.
    state.current_tool_request = None
    state.last_tool_result = None
    state.current_state = RuntimeState.ACTING if replacement_steps else RuntimeState.COMPLETED

    _append_step_record(
        state,
        node_name="replanner",
        summary="Replanner replaced the current/pending trajectory after actor replan request.",
    )
    return state



# Keep the planner output within the configured semantic step budget when one exists.
def _enforce_plan_step_budget(
    *,
    plan_steps: list[PlanStep],
    max_steps: int | None,
) -> list[PlanStep]:
    if max_steps is None:
        return list(plan_steps)
    return list(plan_steps[:max_steps])


# Collapse over-decomposed source-plus-short-summary plans into a proportional trajectory.
def _compact_small_source_summary_plan(
    *,
    task_state: TaskState,
    plan_steps: list[PlanStep],
) -> list[PlanStep]:
    if len(plan_steps) <= 2:
        return plan_steps
    if not _task_is_small_source_summary(task_state=task_state):
        return plan_steps
    if not _plan_looks_like_extraction_pipeline(plan_steps=plan_steps):
        return plan_steps

    return [
        PlanStep(
            step_id="step_1",
            objective=(
                "Identify official sources and collect only the high-level evidence needed "
                "for the requested short summary."
            ),
            rationale=(
                "The task asks for official references and a brief answer, so source selection "
                "and minimal evidence gathering should stay in one bounded semantic step."
            ),
            expected_outcome=(
                "A compact set of official source references plus enough high-level source "
                "content or metadata to support a concise summary."
            ),
        ),
        PlanStep(
            step_id="step_2",
            objective=(
                "Produce the short source-grounded summary with references, staying at the "
                "precision level supported by the observed evidence."
            ),
            rationale=(
                "A short user-facing summary should not become a separate extraction pipeline "
                "unless the user asks for granular legal or technical detail."
            ),
            expected_outcome=(
                "A brief summary and official source list, with no unsupported thresholds, "
                "article-level claims, or granular obligations."
            ),
        ),
    ]


# Detect small research tasks whose deliverable is official sources plus a brief summary.
def _task_is_small_source_summary(*, task_state: TaskState) -> bool:
    task_text = " ".join(
        part
        for part in (
            task_state.task_spec.goal,
            task_state.task_spec.context,
            " ".join(task_state.task_spec.constraints),
            " ".join(task_state.task_spec.success_criteria),
        )
        if part
    ).lower()
    source_markers = ("source", "sources", "reference", "references", "official")
    summary_markers = ("short summary", "brief summary", "concise summary", "short answer")
    detailed_markers = (
        "threshold",
        "thresholds",
        "article-level",
        "full legal text",
        "compliance mapping",
        "detailed analysis",
    )
    return (
        any(marker in task_text for marker in source_markers)
        and any(marker in task_text for marker in summary_markers)
        and not any(marker in task_text for marker in detailed_markers)
    )


# Identify the common over-decomposition pattern: sources, extraction, then synthesis.
def _plan_looks_like_extraction_pipeline(*, plan_steps: list[PlanStep]) -> bool:
    plan_text = " ".join(
        " ".join((step.objective, step.rationale, step.expected_outcome))
        for step in plan_steps
    ).lower()
    return (
        any(marker in plan_text for marker in ("source", "sources", "references"))
        and any(marker in plan_text for marker in ("extract", "extraction", "capture"))
        and any(marker in plan_text for marker in ("summary", "summar", "synthesis", "draft"))
    )


# Keep step history mutation consistent across nodes after bootstrap.
def _append_step_record(state: TaskState, *, node_name: str, summary: str) -> None:
    state.step_index += 1
    state.step_history.append(
        StepRecord(
            step_index=state.step_index,
            runtime_state=state.current_state,
            node_name=node_name,
            summary=summary,
        )
    )


# Treat a missing max_tool_calls limit as unbounded rather than as a runtime error.
def _tool_call_budget_exhausted(*, state: TaskState) -> bool:
    max_tool_calls = state.execution_policy.budget.max_tool_calls
    if max_tool_calls is None:
        return False

    return state.tool_call_count >= max_tool_calls


# Block repeated equivalent tool requests within the same plan step.
def _duplicate_tool_request_error(*, state: TaskState) -> str | None:
    request = state.current_tool_request
    if request is None:
        return None

    fingerprint = _tool_request_fingerprint(request)
    if fingerprint is None:
        return None

    for prior_result in state.tool_results_history:
        prior_request = prior_result.request
        if prior_request.plan_step_id != request.plan_step_id:
            continue
        if _tool_request_fingerprint(prior_request) == fingerprint:
            return (
                "The same tool request was already executed for this plan step; "
                "the actor must use existing evidence, choose a different source, or finish/replan."
            )
    return None


# Build a stable duplicate-detection key for requests that should not be repeated.
def _tool_request_fingerprint(tool_request: ToolRequest) -> tuple[str, str] | None:
    if tool_request.url:
        return (tool_request.tool_name.value, tool_request.url.strip().lower())
    if tool_request.query:
        return (tool_request.tool_name.value, tool_request.query.strip().lower())
    return None


# Persist the local execution trace for the current step before the graph continues or stops.
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

    if actor_result.step_validation is None:
        raise ValueError("Completing a plan step requires step_validation.")

    validation = actor_result.step_validation

    if validation.next_action in {
        StepValidationDecision.CONTINUE_ACTING,
        StepValidationDecision.REQUEST_MORE_EVIDENCE,
    }:
        # A local output can be useful progress without satisfying the semantic objective.
        current_step.status = PlanStepStatus.IN_PROGRESS
        return

    if validation.next_action == StepValidationDecision.ASK_CLARIFICATION:
        current_step.status = PlanStepStatus.BLOCKED
        state.current_tool_request = None
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        return

    if validation.next_action == StepValidationDecision.NEEDS_REPLAN:
        current_step.status = PlanStepStatus.BLOCKED
        state.current_tool_request = None
        state.current_state = RuntimeState.REPLANNING
        return

    if not (
        validation.next_action == StepValidationDecision.COMPLETE_STEP
        and validation.objective_satisfied
        and validation.evidence_sufficient
    ):
        # Invalid or low-confidence completion attempts are treated as continued work.
        current_step.status = PlanStepStatus.IN_PROGRESS
        return

    current_step.status = PlanStepStatus.COMPLETED
    state.executed_steps.append(
        ExecutedPlanStep(
            step_id=current_step.step_id,
            objective=current_step.objective,
            result_type=actor_result.result_type,
            summary=actor_result.summary,
            output_text=actor_result.output_text,
        )
    )

    # Once a step is completed, the last tool result becomes historical context rather than live intent.
    state.current_tool_request = None
    state.last_tool_result = None

    if actor_result.result_type == ActorResultType.FINAL_OUTPUT:
        state.final_output_text = actor_result.output_text
        state.current_plan_step_index = None
        return

    _advance_to_next_plan_step(state)
    # Promote the last completed step output into the canonical final output slot.
    if state.current_plan_step_index is None and actor_result.output_text:
        state.final_output_text = actor_result.output_text


# Keep the external communication subflow explicit even before outbound execution exists.
def _update_external_communication_after_actor_result(
    state: TaskState,
    *,
    actor_result: ActorResult,
) -> None:
    if state.task_spec.domain_type != DomainType.EXTERNAL_COMMUNICATION:
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
            phase=ExternalCommunicationPhase.BLOCKED,
            summary="The external communication branch is blocked pending operator resolution.",
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


# Update the external communication phase only for tasks that actually use that branch.
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


# Decide whether the current actor output is ready for governed human review.
def _external_communication_output_is_review_ready(
    state: TaskState,
    *,
    actor_result: ActorResult,
) -> bool:
    current_step = state.current_plan_step
    validation = actor_result.step_validation
    if current_step is None or validation is None:
        return False

    if actor_result.result_type not in {
        ActorResultType.INTERMEDIATE_OUTPUT,
        ActorResultType.FINAL_OUTPUT,
    }:
        return False

    if not (
        validation.next_action == StepValidationDecision.COMPLETE_STEP
        and validation.objective_satisfied
        and validation.evidence_sufficient
    ):
        return False

    if state.current_plan_step_index == len(state.plan_steps) - 1:
        return True

    semantic_text = " ".join(
        filter(
            None,
            [
                current_step.objective,
                current_step.expected_outcome,
                current_step.rationale,
            ],
        )
    ).lower()
    readiness_markers = (
        "final draft",
        "finalized email draft",
        "ready to send",
        "suitable for immediate sending",
        "complete email draft",
        "full email draft",
        "compose the email",
    )
    return any(marker in semantic_text for marker in readiness_markers)


# Preserve external communication content as one markdown body owned by the actor.
def _merge_external_communication_draft(
    state: TaskState,
    *,
    output_text: str,
    review_ready: bool,
) -> None:
    if state.external_communication is None:
        return

    current_draft = state.external_communication.draft
    metadata = (
        dict(current_draft.metadata)
        if current_draft is not None
        else {"source": "actor_output"}
    )
    metadata["content_format"] = "markdown"
    metadata["review_ready"] = review_ready

    state.external_communication.draft = ExternalCommunicationDraft(
        channel=current_draft.channel if current_draft is not None else "external_communication",
        recipient=current_draft.recipient if current_draft is not None else None,
        subject=current_draft.subject if current_draft is not None else None,
        body=output_text,
        metadata=metadata,
    )


# Advance the runtime cursor to the next semantic step after a completed execution.
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


# Convert actor result categories into explicit runtime outcomes for the current baseline.
def _map_actor_result_to_runtime_state(
    state: TaskState,
    *,
    actor_result: ActorResult,
) -> RuntimeState:
    if actor_result.result_type == ActorResultType.NEEDS_REPLAN:
        return RuntimeState.REPLANNING

    if actor_result.result_type in {
        ActorResultType.ASK_CLARIFICATION,
        ActorResultType.BLOCKED,
    }:
        return RuntimeState.WAITING_HUMAN_REVIEW

    if actor_result.result_type == ActorResultType.PROPOSE_ACTION:
        return RuntimeState.READY_TOOL_EXECUTION

    if actor_result.step_validation is not None:
        # StepValidation is the runtime-facing self-check that decides whether to continue,
        # escalate, replan, or advance after local actor output.
        if actor_result.step_validation.next_action in {
            StepValidationDecision.CONTINUE_ACTING,
            StepValidationDecision.REQUEST_MORE_EVIDENCE,
        }:
            return RuntimeState.ACTING
        if actor_result.step_validation.next_action == StepValidationDecision.ASK_CLARIFICATION:
            return RuntimeState.WAITING_HUMAN_REVIEW
        if actor_result.step_validation.next_action == StepValidationDecision.NEEDS_REPLAN:
            return RuntimeState.REPLANNING
        if actor_result.step_validation.next_action == StepValidationDecision.COMPLETE_STEP:
            if (
                actor_result.step_validation.objective_satisfied
                and actor_result.step_validation.evidence_sufficient
                and state.current_plan_step_index is None
            ):
                return RuntimeState.COMPLETED
            if (
                actor_result.step_validation.objective_satisfied
                and actor_result.step_validation.evidence_sufficient
            ):
                return RuntimeState.READY_NEXT_STEP
            return RuntimeState.ACTING

    if actor_result.result_type == ActorResultType.FINAL_OUTPUT:
        return RuntimeState.COMPLETED

    if state.current_plan_step_index is None:
        return RuntimeState.COMPLETED

    return RuntimeState.READY_NEXT_STEP


# Convert the governance decision into the next runtime state while preserving the actor trace.
def _apply_governance_decision(
    state: TaskState,
    *,
    governance_decision: GovernanceDecision,
    policy_input: PolicyEvaluationInput,
) -> None:
    if governance_decision.directive == GovernanceDirective.STOP:
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.BLOCKED,
            summary="Governance denied the external communication branch.",
            review_ready=False,
        )
        state.current_state = RuntimeState.ABORTED
        state.current_tool_request = None
        state.pending_human_review_request = None
        state.human_review_resume_state = None
        return

    if governance_decision.directive in {
        GovernanceDirective.WAIT_HUMAN_REVIEW,
        GovernanceDirective.ASK_USER_CLARIFICATION,
    }:
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED,
            summary="The draft proposal reached a typed human review gate before any external execution.",
            review_ready=True,
        )
        state.human_review_resume_state = _infer_human_review_resume_state(
            state=state,
            policy_input=policy_input,
        )
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        return

    if governance_decision.directive == GovernanceDirective.REPLAN:
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.EVIDENCE_COLLECTION,
            summary="Governance redirected the external communication branch back to replanning.",
            review_ready=False,
        )
        state.pending_human_review_request = None
        state.human_review_resume_state = None
        state.current_state = RuntimeState.REPLANNING
        state.current_tool_request = None
        return

    if governance_decision.directive == GovernanceDirective.REQUEST_MORE_EVIDENCE:
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.EVIDENCE_COLLECTION,
            summary="Governance required more evidence before the external communication draft can progress.",
            review_ready=False,
        )
        state.pending_human_review_request = None
        state.human_review_resume_state = None
        _restore_actor_step_for_more_evidence(state)
        state.current_state = RuntimeState.ACTING
        state.current_tool_request = None
        return

    if policy_input.actor_result_type == ActorResultType.PROPOSE_ACTION:
        state.current_state = RuntimeState.READY_TOOL_EXECUTION
        return

    if policy_input.actor_result_type == ActorResultType.NEEDS_REPLAN:
        state.pending_human_review_request = None
        state.human_review_resume_state = None
        state.current_state = RuntimeState.REPLANNING
        return

    if policy_input.actor_result_type == ActorResultType.ASK_CLARIFICATION:
        state.human_review_resume_state = _infer_human_review_resume_state(
            state=state,
            policy_input=policy_input,
        )
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        return

    if policy_input.actor_result_type == ActorResultType.BLOCKED:
        state.human_review_resume_state = _infer_human_review_resume_state(
            state=state,
            policy_input=policy_input,
        )
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        return

    if policy_input.actor_result_type in {
        ActorResultType.INTERMEDIATE_OUTPUT,
        ActorResultType.FINAL_OUTPUT,
    }:
        return

    state.current_state = RuntimeState.ACTING


# Undo a fragile completion attempt so the actor can collect more evidence.
def _restore_actor_step_for_more_evidence(state: TaskState) -> None:
    actor_result = state.actor_result
    if actor_result is None:
        return

    for index, step in enumerate(state.plan_steps):
        if step.step_id == actor_result.plan_step_id:
            step.status = PlanStepStatus.IN_PROGRESS
            state.current_plan_step_index = index
        elif step.status == PlanStepStatus.IN_PROGRESS:
            step.status = PlanStepStatus.PENDING

    state.executed_steps = [
        step for step in state.executed_steps if step.step_id != actor_result.plan_step_id
    ]
    if state.final_output_text == actor_result.output_text:
        state.final_output_text = None


# Derive the state the runtime should return to after a human review approval.
def _infer_human_review_resume_state(
    *,
    state: TaskState,
    policy_input: PolicyEvaluationInput,
) -> RuntimeState:
    if state.current_tool_request is not None:
        return RuntimeState.READY_TOOL_EXECUTION

    if policy_input.actor_result_type == ActorResultType.ASK_CLARIFICATION:
        return RuntimeState.ACTING

    if policy_input.actor_result_type == ActorResultType.BLOCKED:
        return RuntimeState.ACTING

    if policy_input.actor_result_type == ActorResultType.PROPOSE_ACTION:
        return RuntimeState.READY_TOOL_EXECUTION

    if state.current_plan_step_index is None:
        return RuntimeState.COMPLETED if state.executed_steps else RuntimeState.PLANNING

    if state.current_state == RuntimeState.READY_NEXT_STEP:
        return RuntimeState.READY_NEXT_STEP

    return RuntimeState.ACTING


# Convert a tool result into explicit evidence objects that later actor turns can consume.
def _build_observed_evidence_from_tool_result(
    *,
    state: TaskState,
    tool_result: ToolExecutionResult,
) -> list[ObservedEvidence]:
    current_step = state.current_plan_step
    if current_step is None:
        return []

    evidence_items: list[ObservedEvidence] = []
    if not tool_result.success:
        state.scratchpad.append(
            _build_observation_scratchpad_entry(
                state=state,
                tool_result=tool_result,
                evidence_refs=[],
            )
        )
        return evidence_items

    # Preserve source-level provenance whenever the tool returned it.
    for index, source in enumerate(tool_result.sources, start=1):
        # Avoid cloning one global tool transcript onto every returned source.
        excerpt = source.snippet
        if excerpt is None and len(tool_result.sources) == 1:
            excerpt = tool_result.content or tool_result.summary
        if not _is_meaningful_evidence_excerpt(excerpt):
            continue
        evidence_items.append(
            ObservedEvidence(
                evidence_id=f"{current_step.step_id}:tool:{state.tool_call_count}:source:{index}",
                plan_step_id=current_step.step_id,
                title=source.title,
                locator=source.locator,
                excerpt=(excerpt or "")[:1000],
                source_tool=tool_result.request.tool_name,
                support_level=_classify_observed_evidence_support_level(
                    tool_name=tool_result.request.tool_name,
                    locator=source.locator,
                    title=source.title,
                    excerpt=excerpt or "",
                ),
            )
        )

    # Keep at least one fallback evidence item even when the tool returned no structured sources.
    if not evidence_items:
        locator = tool_result.request.url or tool_result.request.query or tool_result.request.tool_name.value
        excerpt = tool_result.content or tool_result.error_message or tool_result.summary
        if not _is_meaningful_evidence_excerpt(excerpt):
            state.scratchpad.append(
                _build_observation_scratchpad_entry(
                    state=state,
                    tool_result=tool_result,
                    evidence_refs=[],
                )
            )
            return evidence_items
        evidence_items.append(
            ObservedEvidence(
                evidence_id=f"{current_step.step_id}:tool:{state.tool_call_count}:fallback",
                plan_step_id=current_step.step_id,
                title=tool_result.request.tool_name.value,
                locator=locator,
                excerpt=(excerpt or "")[:1000],
                source_tool=tool_result.request.tool_name,
                support_level=_classify_observed_evidence_support_level(
                    tool_name=tool_result.request.tool_name,
                    locator=locator,
                    title=tool_result.request.tool_name.value,
                    excerpt=excerpt or "",
                ),
            )
        )

    state.scratchpad.append(
        _build_observation_scratchpad_entry(
            state=state,
            tool_result=tool_result,
            evidence_refs=[item.evidence_id for item in evidence_items],
        )
    )

    return evidence_items


# Exclude empty/status-only strings from evidence used by later actor turns.
def _is_meaningful_evidence_excerpt(value: str | None) -> bool:
    if value is None:
        return False
    normalized = " ".join(value.split())
    if not normalized:
        return False

    lowered = normalized.lower()
    if lowered in {
        "url text fetched successfully.",
        "web search executed successfully.",
        "tool succeeded.",
    }:
        return False

    word_count = len(normalized.split())
    if word_count >= 8:
        return True

    if "http://" in lowered or "https://" in lowered:
        return True

    if any(
        marker in lowered
        for marker in (
            "official source",
            "official sources",
            "source shortlist",
            "eur-lex",
            "european commission",
            "portal",
            "legislation page",
            "purpose",
            "scope",
            "overview",
            "summary",
            "obligation",
            "criteria",
            "threshold",
            "deadline",
            "article ",
            "must ",
            "shall ",
            "required to",
            "interoperability",
            "data portability",
            "entered into force",
            "applies from",
        )
    ):
        return len(normalized) >= 30

    return len(normalized) >= 80


# Detect URL fetch failures that show the runtime could not extract readable text from a source.
def _tool_result_indicates_extractability_limit(tool_result: ToolExecutionResult) -> bool:
    if tool_result.request.tool_name != ToolName.FETCH_URL_TEXT:
        return False
    if tool_result.success:
        return False

    failure_text = " ".join(
        part.strip().lower()
        for part in (tool_result.summary, tool_result.error_message)
        if part and part.strip()
    )
    return "extracted readable text was empty" in failure_text


# Classify how much claim precision one observed evidence item can safely support.
def _classify_observed_evidence_support_level(
    *,
    tool_name: ToolName,
    locator: str,
    title: str,
    excerpt: str,
) -> EvidenceSupportLevel:
    lowered_locator = locator.lower()
    lowered_title = title.lower()
    lowered_excerpt = excerpt.lower()
    timeline_markers = (
        "entered into force",
        "comes into force",
        "coming into force",
        "effective date",
        "effective from",
        "applies from",
        "applies on",
        "entry into force",
        "adopted on",
        "published on",
        "implementation date",
    )

    if any(marker in lowered_excerpt for marker in timeline_markers):
        if tool_name == ToolName.WEB_SEARCH:
            return EvidenceSupportLevel.DETAILED_EXTRACTED
        if tool_name == ToolName.FETCH_URL_TEXT:
            return EvidenceSupportLevel.DETAILED_EXTRACTED

    if tool_name == ToolName.WEB_SEARCH:
        high_level_markers = (
            "official pages",
            "official website",
            "overview",
            "what the ",
            "purpose",
            "gatekeepers",
            "core platform services",
            "obligations",
            "prohibitions",
            "legislation",
            "website",
            "summary",
            "aim",
        )
        if any(marker in lowered_excerpt for marker in high_level_markers):
            return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY
        if lowered_title == tool_name.value:
            return EvidenceSupportLevel.SOURCE_DISCOVERY
        if len(" ".join(excerpt.split())) >= 40:
            return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY
        return EvidenceSupportLevel.SOURCE_DISCOVERY

    if tool_name != ToolName.FETCH_URL_TEXT:
        return EvidenceSupportLevel.DETAILED_EXTRACTED

    if "eur-lex.europa.eu" in lowered_locator and "/summary/" not in lowered_locator:
        return EvidenceSupportLevel.PRIMARY_LEGAL

    if any(
        marker in lowered_locator
        for marker in (
            "/summary/",
            "legislation_en",
            "index_en",
            "gatekeepers_en",
        )
    ):
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

    detailed_markers = (
        "article ",
        "articles ",
        "must ",
        "shall ",
        "required to",
        "prohibited",
        "obligations",
        "prohibitions",
        "criteria",
        "threshold",
        "deadline",
        "interoperability",
        "data portability",
    )
    if sum(1 for marker in detailed_markers if marker in lowered_excerpt) >= 2:
        return EvidenceSupportLevel.DETAILED_EXTRACTED

    if "regulation" in lowered_title and "summary" not in lowered_title:
        return EvidenceSupportLevel.DETAILED_EXTRACTED

    return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY


# Record tool observations as audit-safe working memory for later actor turns.
def _build_observation_scratchpad_entry(
    *,
    state: TaskState,
    tool_result: ToolExecutionResult,
    evidence_refs: list[str],
) -> ScratchpadEntry:
    current_step = state.current_plan_step
    if current_step is None:
        raise ValueError("Cannot create observation scratchpad entry without a current plan step.")

    return ScratchpadEntry(
        turn_index=state.actor_turn_count,
        plan_step_id=current_step.step_id,
        entry_type=ScratchpadEntryType.OBSERVATION,
        summary=f"Observed tool result from '{tool_result.request.tool_name.value}': {tool_result.summary}",
        evidence_refs=evidence_refs,
    )


# Keep the closing trace message aligned with the actual endpoint reached by the runtime.
def _build_finalize_summary(state: TaskState) -> str:
    if state.actor_result is None:
        return "Layer 2 stopped before actor execution because no operational outcome was available."

    if state.current_state == RuntimeState.ABORTED:
        return (
            "Layer 2 stopped after governance denied the actor outcome "
            f"'{state.actor_result.result_type.value}'."
        )

    return (
        "Layer 2 stopped after the planner/actor/tool loop "
        f"with actor result '{state.actor_result.result_type.value}', "
        f"runtime state '{state.current_state.value}', "
        f"actor turns {state.actor_turn_count}, "
        f"and tool calls {state.tool_call_count}."
    )


__all__ = [
    "actor",
    "bootstrap_from_intake",
    "finalize_bootstrap",
    "governance",
    "human_review",
    "observe_tool_result",
    "planner",
    "tool_gateway",
    "replanner",
]
