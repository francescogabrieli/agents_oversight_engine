from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .state import PlanStep, PlanStepStatus, TaskState


@dataclass(frozen=True)
class ReplanDecision:
    # The replanner replaces only the current/pending trajectory, never completed history.
    replacement_steps: list[PlanStep]
    rationale: str
    replanner_name: str
    prompt_version: str


class ReplannerPort(Protocol):
    # Replanner boundary keeps replan logic swappable and testable.
    replanner_name: str
    prompt_version: str

    def replan(self, *, task_state: TaskState) -> ReplanDecision: ...


class LocalConservativeReplanner(ReplannerPort):
    # This default is intentionally deterministic until an LLM-backed replanner is introduced.
    replanner_name = "local_conservative_replanner"
    prompt_version = "replanner_v1"

    # Produce a compact recovery step from the actor blocking reason.
    def replan(self, *, task_state: TaskState) -> ReplanDecision:
        actor_result = task_state.actor_result
        current_step = task_state.current_plan_step

        if actor_result is None or current_step is None:
            raise ValueError("Cannot replan without an actor result and current plan step.")

        reason = actor_result.blocking_reason or actor_result.summary
        # Preserve the original objective in the recovery step so drift is visible in traces.
        replacement_step = PlanStep(
            step_id=f"{current_step.step_id}_replanned",
            objective=f"Recover from the blocked step: {current_step.objective}",
            rationale=f"The actor requested replanning because: {reason}",
            expected_outcome="A revised executable step that lets the runtime continue safely.",
            status=PlanStepStatus.PENDING,
        )

        return ReplanDecision(
            replacement_steps=[replacement_step],
            rationale="Replaced the blocked current trajectory with one conservative recovery step.",
            replanner_name=self.replanner_name,
            prompt_version=self.prompt_version,
        )


# Build the default replanner for the current runtime slice.
def build_default_replanner() -> ReplannerPort:
    return LocalConservativeReplanner()
