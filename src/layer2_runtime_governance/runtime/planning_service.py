from layer1_intake import RequestedOperationType

from ..planning import PlannerPort
from ..domain import PlanStep, PlanStepStatus, RuntimeState, TaskState
from .lifecycle_service import _append_step_record


# Materialize only semantic planner output; runtime never drops trailing steps by slicing.
def planner(state: TaskState, *, planner_service: PlannerPort) -> TaskState:
    if state.current_state != RuntimeState.PLANNING:
        _append_step_record(state, node_name="planner", summary="Planning skipped outside planning state.")
        return state

    decision = planner_service.plan(task_state=state)
    planned_steps = _compact_external_communication_plan(
        task_state=state,
        plan_steps=decision.plan_steps,
    )
    planned_steps = _compact_small_source_summary_plan(
        task_state=state,
        plan_steps=planned_steps,
    )
    state.plan_steps = _ensure_requested_operation_step(
        task_state=state,
        plan_steps=planned_steps,
    )
    state.current_plan_step_index = 0 if state.plan_steps else None
    state.planner_rationale = decision.rationale
    state.planner_name = decision.planner_name
    state.planner_prompt_version = decision.prompt_version
    if state.current_plan_step is not None:
        state.current_plan_step.status = PlanStepStatus.IN_PROGRESS

    summary = "Initial semantic full plan created from the current task state."
    if state.plan_steps != decision.plan_steps:
        summary += " Plan was normalized to the task scope."
    _append_step_record(state, node_name="planner", summary=summary)
    return state


# Ensure a requested communication effect has a semantic drafting step without using domain_type as a gate.
def _ensure_requested_operation_step(*, task_state: TaskState, plan_steps: list[PlanStep]) -> list[PlanStep]:
    requested_email = next(
        (
            operation
            for operation in task_state.task_spec.requested_operations
            if operation.operation_type == RequestedOperationType.EXTERNAL_COMMUNICATION
            and operation.channel == "email"
        ),
        None,
    )
    if requested_email is None:
        return list(plan_steps)
    if any("email" in step.objective.lower() or "communication" in step.objective.lower() for step in plan_steps):
        return list(plan_steps)
    return [
        *plan_steps,
        PlanStep(
            step_id=f"step_{len(plan_steps) + 1}",
            objective="Prepare the requested email draft without sending it.",
            rationale="The requested external effect requires a typed draft before governance and human review.",
            expected_outcome="A complete email proposal with recipient, subject, and body for governed review.",
        ),
    ]


# Collapse communication meta-steps while retaining a genuine evidence step when needed.
def _compact_external_communication_plan(
    *,
    task_state: TaskState,
    plan_steps: list[PlanStep],
) -> list[PlanStep]:
    if not _has_requested_email(task_state=task_state) or len(plan_steps) <= 1:
        return list(plan_steps)

    evidence_steps = [step for step in plan_steps if _looks_like_evidence_step(step=step)]
    draft_step = PlanStep(
        step_id="step_2" if evidence_steps else "step_1",
        objective=(
            "Produce one complete requested external communication draft with explicit "
            "channel, recipient, subject, and body without sending it."
        ),
        rationale=(
            "Draft construction is one semantic operation; governance evaluates the "
            "evaluate the normalized proposal after the actor produces it."
        ),
        expected_outcome=(
            "A complete typed email proposal ready for governed review with no external side effect."
        ),
    )
    if not evidence_steps:
        return [draft_step]
    evidence_step = evidence_steps[-1]
    return [
        PlanStep(
            step_id="step_1",
            objective=evidence_step.objective,
            rationale=evidence_step.rationale,
            expected_outcome=evidence_step.expected_outcome,
        ),
        draft_step,
    ]


# Compact an over-decomposed source/extraction/summary pipeline using semantic wording only.
def _compact_small_source_summary_plan(
    *,
    task_state: TaskState,
    plan_steps: list[PlanStep],
) -> list[PlanStep]:
    if len(plan_steps) <= 2 or not _task_is_small_source_summary(task_state=task_state):
        return list(plan_steps)
    if not _plan_looks_like_extraction_pipeline(plan_steps=plan_steps):
        return list(plan_steps)
    return [
        PlanStep(
            step_id="step_1",
            objective="Identify official sources and collect the high-level evidence needed for the short summary.",
            rationale="Source selection and minimal evidence gathering belong in one bounded semantic step.",
            expected_outcome="A compact set of official references and enough content for a concise summary.",
        ),
        PlanStep(
            step_id="step_2",
            objective="Produce the short source-grounded summary with references at the precision supported by observed evidence.",
            rationale="A brief answer should not become a separate extraction pipeline without a request for granular detail.",
            expected_outcome="A brief summary and official source list without unsupported granular claims.",
        ),
    ]


# Detect whether the task explicitly asks for an email operation.
def _has_requested_email(*, task_state: TaskState) -> bool:
    return any(
        operation.operation_type == RequestedOperationType.EXTERNAL_COMMUNICATION
        and operation.channel == "email"
        for operation in task_state.task_spec.requested_operations
    )


# Detect genuine evidence work without reintroducing a rank or step-kind contract.
def _looks_like_evidence_step(*, step: PlanStep) -> bool:
    text = step.objective.lower()
    return any(
        marker in text
        for marker in ("source", "sources", "discover", "discovery", "extract", "extraction", "collect")
    )


# Detect a small source-plus-summary trajectory from task semantics.
def _task_is_small_source_summary(*, task_state: TaskState) -> bool:
    text = " ".join(
        (
            task_state.task_spec.goal,
            task_state.task_spec.context,
            " ".join(task_state.task_spec.constraints),
            " ".join(task_state.task_spec.success_criteria),
        )
    ).lower()
    return (
        any(marker in text for marker in ("source", "sources", "reference", "official"))
        and any(marker in text for marker in ("short summary", "brief summary", "concise summary", "short answer"))
        and not any(marker in text for marker in ("threshold", "article-level", "detailed analysis", "full legal text"))
    )


# Detect source discovery, extraction, and synthesis being split artificially.
def _plan_looks_like_extraction_pipeline(*, plan_steps: list[PlanStep]) -> bool:
    text = " ".join(
        " ".join((step.objective, step.rationale, step.expected_outcome))
        for step in plan_steps
    ).lower()
    return (
        any(marker in text for marker in ("source", "sources", "reference"))
        and any(marker in text for marker in ("extract", "extraction", "capture"))
        and any(marker in text for marker in ("summary", "summar", "synthesis", "draft"))
    )


__all__ = [
    "planner",
    "_ensure_requested_operation_step",
    "_compact_external_communication_plan",
    "_compact_small_source_summary_plan",
    "_task_is_small_source_summary",
    "_plan_looks_like_extraction_pipeline",
]
