from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .actor_contract import ActorTask
from .state import RuntimeState

# GovernanceEventType names the observable events emitted while the actor works.
class GovernanceEventType(str, Enum):
    STEP_STARTED = "step_started"
    INTENT_UPDATE = "intent_update"
    EXTERNAL_ACTION_REQUESTED = "external_action_requested"
    EVIDENCE_UPDATED = "evidence_updated"
    UNCERTAINTY_SIGNAL = "uncertainty_signal"
    DRAFT_OUTPUT_READY = "draft_output_ready"
    STEP_COMPLETED = "step_completed"
    STEP_BLOCKED = "step_blocked"

# GovernanceDirectiveType is the external control surface of the Governance Core.
class GovernanceDirectiveType(str, Enum):
    CONTINUE = "continue"
    CONTINUE_READ_ONLY = "continue_read_only"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    REPLAN = "replan"
    WAIT_HUMAN_REVIEW = "wait_human_review"
    ASK_USER_CLARIFICATION = "ask_user_clarification"
    STOP = "stop"

# GovernanceSignalSource records which subsystem contributed to a governance decision.
class GovernanceSignalSource(str, Enum):
    POLICY_ENGINE = "policy_engine"
    CONFIDENCE_RISK_TRAJECTORY = "confidence_risk_trajectory"
    JUDGE = "judge"


@dataclass(frozen=True)
class GovernanceEvent:
    # The actor emits compact operational events while executing the current step.
    event_index: int
    plan_step_id: str
    plan_step_index: int
    event_type: GovernanceEventType
    summary: str
    current_state: RuntimeState
    external_impact: bool = False
    uncertainty_signals: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class GovernanceStreamSnapshot:
    # The Governance Core evaluates a recent window of the live operational stream.
    actor_task: ActorTask
    recent_events: tuple[GovernanceEvent, ...]
    latest_event: GovernanceEvent


@dataclass(frozen=True)
class GovernanceDirective:
    # The Governance Core returns one control decision for the runtime.
    directive_type: GovernanceDirectiveType
    rationale: str
    sources: tuple[GovernanceSignalSource, ...]
    human_review_reason: str | None = None


class GovernancePort(Protocol):
    # The port represents the Governance Core entrypoint while internals stay free to evolve.
    def evaluate(self, *, snapshot: GovernanceStreamSnapshot) -> GovernanceDirective: ...


# Build a validated Governance Core input snapshot from the actor context and event stream.
def build_governance_stream_snapshot(
    *,
    actor_task: ActorTask,
    event_history: tuple[GovernanceEvent, ...],
) -> GovernanceStreamSnapshot:
    # Governance always evaluates a non-empty slice of the actor event stream.
    if not event_history:
        raise ValueError("Cannot build GovernanceStreamSnapshot without event_history.")

    latest_event = event_history[-1]

    # The latest event must still belong to the step the actor is executing now.
    if latest_event.plan_step_id != actor_task.current_plan_step.step_id:
        raise ValueError(
            "latest_event.plan_step_id does not match actor_task.current_plan_step.step_id."
        )

    # Keep event ordering aligned with the planner-selected step index.
    if latest_event.plan_step_index != actor_task.current_plan_step_index:
        raise ValueError(
            "latest_event.plan_step_index does not match actor_task.current_plan_step_index."
        )

    return GovernanceStreamSnapshot(
        actor_task=actor_task,
        recent_events=event_history,
        latest_event=latest_event,
    )


__all__ = [
    "GovernanceDirective",
    "GovernanceDirectiveType",
    "GovernanceEvent",
    "GovernanceEventType",
    "GovernancePort",
    "GovernanceSignalSource",
    "GovernanceStreamSnapshot",
    "build_governance_stream_snapshot",
]
