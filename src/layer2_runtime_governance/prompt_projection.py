from __future__ import annotations

from datetime import datetime

from layer3_tool_bus_external_capabilities.tool_contract import ToolObservation

from .domain import PlanStep, ScratchpadEntry


# Project one semantic plan step with the exact model-visible audit fields.
def project_prompt_plan_step(*, step: PlanStep) -> dict[str, object]:
    evidence_burden = getattr(step, "evidence_burden", None)
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
        "evidence_burden": (
            {
                "min_independent_sources": evidence_burden.min_independent_sources,
                "require_retrieved_content": evidence_burden.require_retrieved_content,
                "freshness_window_days": evidence_burden.freshness_window_days,
            }
            if evidence_burden is not None
            else None
        ),
    }


# Project only concrete observation content and its stable provenance handles.
def project_prompt_observation(
    *, observation: ToolObservation
) -> dict[str, object]:
    return {
        "observation_id": observation.observation_id,
        "locator": observation.locator,
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
