from ..domain import (
    PlanStep,
    PlanStepStatus,
    ReplanCheckpoint,
    RuntimeState,
    TaskState,
    semantic_plan_signature,
)
from ..planning.replanning import ReplannerPort

# Nodes.py contains the executable Layer 2 business logic.
# At this stage the runtime performs bootstrap, planning, and the actor/tool loop.
from .lifecycle_service import _append_step_record

# Replace only the current and pending semantic trajectory.
def replanner(state: TaskState, *, replanner_service: ReplannerPort) -> TaskState:
    # A replacement semantic trajectory invalidates every not-yet-executed capability request.
    state.current_tool_request = None
    state.pending_tool_requests = []
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

    replacement_steps = _dedupe_replacement_step_ids(
        replacement_steps=list(decision.replacement_steps),
        existing_step_ids={step.step_id for step in completed_steps},
    )
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
    state.replan_checkpoints.append(
        ReplanCheckpoint(
            actor_turn_count=state.actor_turn_count,
            tool_result_count=len(state.tool_results_history),
            observation_count=len(state.observations),
            completed_step_count=len(state.executed_steps),
            human_review_count=len(state.human_review_history),
            plan_signature=semantic_plan_signature(state.plan_steps),
            request_reason=(
                state.actor_result.blocking_reason
                if state.actor_result is not None
                and state.actor_result.blocking_reason is not None
                else ""
            ),
            progress_epoch=state.progress_epoch,
        )
    )
    # Replanning replaces the live trajectory, so stale tool execution context must not leak forward.
    state.current_tool_request = None
    state.last_tool_result = None
    state.current_state = RuntimeState.ACTING if replacement_steps else RuntimeState.COMPLETED
    if replacement_steps:
        state.progress_epoch += 1
        state.state_revision += 1
        state.no_progress_turns = 0

    _append_step_record(
        state,
        node_name="replanner",
        summary="Replanner replaced the current/pending trajectory after actor replan request.",
    )
    return state

# Replace only the current and pending semantic trajectory.
def _dedupe_replacement_step_ids(
    *,
    replacement_steps: list[PlanStep],
    existing_step_ids: set[str],
) -> list[PlanStep]:
    if not replacement_steps:
        return []

    used_ids = set(existing_step_ids)
    next_replan_index = _next_replan_step_index(used_ids)
    normalized_steps: list[PlanStep] = []
    for step in replacement_steps:
        if step.step_id in used_ids:
            step.step_id = f"replan_step_{next_replan_index}"
            next_replan_index += 1
        used_ids.add(step.step_id)
        normalized_steps.append(step)
    return normalized_steps

# Replace only the current and pending semantic trajectory.
def _next_replan_step_index(existing_step_ids: set[str]) -> int:
    max_index = 0
    for step_id in existing_step_ids:
        if not step_id.startswith("replan_step_"):
            continue
        suffix = step_id.removeprefix("replan_step_")
        if suffix.isdigit():
            max_index = max(max_index, int(suffix))
    return max_index + 1

__all__ = [
    "replanner",
    "_dedupe_replacement_step_ids",
    "_next_replan_step_index"
]
