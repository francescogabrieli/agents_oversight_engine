from __future__ import annotations

from datetime import datetime

from layer3_tool_bus_external_capabilities.tool_contract import ToolObservation

from .domain import PlanStep, ScratchpadEntry, project_evidence_burden


# Project one semantic plan step with the exact model-visible audit fields.
def project_prompt_plan_step(*, step: PlanStep) -> dict[str, object]:
    return {
        "step_id": step.step_id,
        "objective": step.objective,
        "rationale": step.rationale,
        "expected_outcome": step.expected_outcome,
        "status": step.status.value,
        "produces_factual_claims": bool(
            getattr(step, "produces_factual_claims", False)
        ),
        "produces_governed_external_effect_proposal": bool(
            getattr(step, "produces_governed_external_effect_proposal", False)
        ),
        "evidence_burden": project_evidence_burden(
            getattr(step, "evidence_burden", None)
        ),
    }


# Project only concrete observation content and its stable provenance handles.
def project_prompt_observation(
    *, observation: ToolObservation
) -> dict[str, object]:
    return {
        "observation_id": observation.observation_id,
        "locator": observation.locator,
        "retrieval_kind": observation.retrieval_kind.value,
        "content": observation.content,
        "source_timestamp": _source_timestamp_text(
            getattr(observation, "source_timestamp", None)
        ),
    }


# Render only a restored, typed source timestamp into an actor-facing prompt.
def _source_timestamp_text(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


# Copy audit-safe scratchpad fields so prompt payloads cannot alias runtime state.
def project_prompt_scratchpad_entry(
    *, entry: ScratchpadEntry
) -> dict[str, str | int | list[str]]:
    return {
        "turn_index": entry.turn_index,
        "plan_step_id": entry.plan_step_id,
        "entry_type": entry.entry_type.value,
        "summary": entry.summary,
        "evidence_refs": list(entry.evidence_refs),
    }


__all__ = [
    "project_prompt_observation",
    "project_prompt_plan_step",
    "project_prompt_scratchpad_entry",
]
