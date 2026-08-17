from __future__ import annotations

from layer3_tool_bus_external_capabilities.tool_contract import ToolObservation

from .domain import PlanStep, ScratchpadEntry


# Project one semantic plan step with the exact model-visible audit fields.
def project_prompt_plan_step(*, step: PlanStep) -> dict[str, str]:
    return {
        "step_id": step.step_id,
        "objective": step.objective,
        "rationale": step.rationale,
        "expected_outcome": step.expected_outcome,
        "status": step.status.value,
    }


# Project only concrete observation content and its stable provenance handles.
def project_prompt_observation(
    *, observation: ToolObservation
) -> dict[str, str | None]:
    return {
        "observation_id": observation.observation_id,
        "locator": observation.locator,
        "content": observation.content,
    }


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
