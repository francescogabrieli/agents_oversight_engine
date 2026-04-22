from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .actor_contract import (
    ActorPort,
    ActorResult,
    ActorResultType,
    ActorTask,
    CapabilityAccessMode,
)

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled at runtime if dependency is missing
    OpenAI = None


@dataclass(frozen=True)
class OpenAIActorSettings:
    model: str = "gpt-5.4-mini"
    api_key: str | None = None
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "OpenAIActorSettings":
        # Merge process env and local .env files into one actor settings object.
        env_file_values = _load_env_file_values()

        return cls(
            model=os.getenv(
                "LAYER2_ACTOR_OPENAI_MODEL",
                env_file_values.get("LAYER2_ACTOR_OPENAI_MODEL", "gpt-5.4-mini"),
            ),
            api_key=os.getenv("OPENAI_API_KEY", env_file_values.get("OPENAI_API_KEY")),
            timeout_seconds=float(
                os.getenv(
                    "LAYER2_ACTOR_OPENAI_TIMEOUT_SECONDS",
                    env_file_values.get("LAYER2_ACTOR_OPENAI_TIMEOUT_SECONDS", "30"),
                )
            ),
        )


class ActorExecutionError(RuntimeError):
    """Raised when the actor cannot produce a valid operational proposal."""


class _ActorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_type: ActorResultType
    summary: str = Field(..., min_length=1)
    capability_mode: CapabilityAccessMode = CapabilityAccessMode.NONE
    external_impact: bool = False
    output_text: str | None = None
    blocking_reason: str | None = None


# Build the constrained system prompt for Layer 2 step execution.
def build_actor_system_prompt() -> str:
    return """
You are the execution agent for layer 2 of a plan-and-execute runtime.
The planner has already produced a multi-step semantic plan.
Your job is to execute only the current plan step, using the prior executed steps as context.

Return a structured object with:
- result_type
- summary
- capability_mode
- external_impact
- output_text
- blocking_reason

Rules:
- Operate only on the current plan step.
- Use the full plan and past executed steps as context, not as a second planning task.
- Do not rewrite the plan and do not invent new plan steps.
- Work in a local ReAct-like way for the current step, but return only the final structured outcome.
- Produce exactly one execution outcome for the current step.
- Keep the outcome compact, explicit, and directly actionable by the later runtime.
- If the step requires external capability access, choose capability_mode:
  - none
  - read_only
  - write
- Set external_impact to true only for externally impactful write-like actions.
- Use result_type:
  - propose_action when the current step requires a later tool or external capability to proceed
  - intermediate_output when the current step can be executed locally and yields a grounded partial output
  - final_output when the step can already complete the task safely
  - ask_clarification when missing information blocks safe progress
  - blocked when the task cannot proceed safely from the current state
  - needs_replan when the current semantic step is misaligned or insufficient
- Do not emit chain-of-thought.
- Do not emit concrete shell commands, API payloads, or raw tool call arguments.
- Do not mention specific URLs unless they are already explicitly part of user-provided context.
- Respect read_only_mode when it is true.
- Prefer read-only evidence collection before synthesis for research or analysis tasks.
- If the current step can be completed from the already executed steps and current context, prefer intermediate_output or final_output with capability_mode none.
""".strip()


# Build the user prompt from the canonical actor handoff.
def build_actor_user_prompt(*, actor_task: ActorTask) -> str:
    payload = {
        "objective": actor_task.task_spec.goal,
        "task_spec": {
            "goal": actor_task.task_spec.goal,
            "context": actor_task.task_spec.context,
            "constraints": actor_task.task_spec.constraints,
            "domain_type": actor_task.task_spec.domain_type.value,
            "domain_label": actor_task.task_spec.domain_label,
            "sensitivity": actor_task.task_spec.sensitivity.value,
            "success_criteria": actor_task.task_spec.success_criteria,
        },
        "execution_policy": {
            "policy_profile_name": actor_task.execution_policy.policy_profile_name,
            "confidence_threshold": actor_task.execution_policy.confidence_threshold,
            "read_only_mode": actor_task.execution_policy.read_only_mode,
            "allowed_tools": actor_task.execution_policy.allowed_tools,
            "disallowed_tools": actor_task.execution_policy.disallowed_tools,
            "budget": {
                "max_steps": actor_task.execution_policy.budget.max_steps,
                "max_tool_calls": actor_task.execution_policy.budget.max_tool_calls,
            },
        },
        "intake_metadata": {
            "parse_status": actor_task.intake_metadata.parse_status.value,
            "warnings": actor_task.intake_metadata.warnings,
            "missing_information": actor_task.intake_metadata.missing_information,
        },
        "runtime": {
            "current_state": actor_task.current_state.value,
            "runtime_flags": [flag.value for flag in actor_task.runtime_flags],
        },
        "planner": {
            "planner_name": actor_task.planner_name,
            "planner_prompt_version": actor_task.planner_prompt_version,
            "planner_rationale": actor_task.planner_rationale,
        },
        "plan": {
            "current_step_number": actor_task.current_plan_step_index + 1,
            "current_plan_step_index": actor_task.current_plan_step_index,
            "current_plan_step": {
                "step_id": actor_task.current_plan_step.step_id,
                "objective": actor_task.current_plan_step.objective,
                "rationale": actor_task.current_plan_step.rationale,
                "expected_outcome": actor_task.current_plan_step.expected_outcome,
                "status": actor_task.current_plan_step.status.value,
            },
            "plan_steps": [
                {
                    "step_id": step.step_id,
                    "objective": step.objective,
                    "rationale": step.rationale,
                    "expected_outcome": step.expected_outcome,
                    "status": step.status.value,
                }
                for step in actor_task.plan_steps
            ],
        },
        "past_steps": [
            {
                "step_id": step.step_id,
                "objective": step.objective,
                "result_type": step.result_type.value,
                "summary": step.summary,
                "output_text": step.output_text,
            }
            for step in actor_task.past_steps
        ],
    }

    return (
        "Execute the current plan step in a plan-and-execute runtime.\n\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}"
    )


class OpenAIActor(ActorPort):
    actor_name = "openai_layer2_actor"
    prompt_version = "actor_v2_plan_execute"

    def __init__(self, settings: OpenAIActorSettings | None = None) -> None:
        # Validate actor prerequisites once at construction time.
        self._settings = settings or OpenAIActorSettings.from_env()

        if OpenAI is None:
            raise RuntimeError(
                "The 'openai' package is not installed. Install it before using the Layer 2 actor."
            )
        if not self._settings.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is missing. Set it in the process environment or in src/.env."
            )

        self._client = OpenAI(
            api_key=self._settings.api_key,
            timeout=self._settings.timeout_seconds,
        )

    # Ask OpenAI for one typed actor proposal and convert it into the domain result.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        try:
            response = self._client.responses.parse(
                model=self._settings.model,
                input=[
                    {
                        "role": "system",
                        "content": build_actor_system_prompt(),
                    },
                    {
                        "role": "user",
                        "content": build_actor_user_prompt(actor_task=actor_task),
                    },
                ],
                text_format=_ActorPayload,
            )
        except Exception as exc:  # pragma: no cover - network/client errors
            raise ActorExecutionError(f"Actor request failed: {exc}") from exc

        payload = getattr(response, "output_parsed", None)
        if payload is None:
            raise ActorExecutionError("Actor returned no structured output.")

        return ActorResult(
            plan_step_id=actor_task.current_plan_step.step_id,
            result_type=payload.result_type,
            summary=payload.summary,
            capability_mode=payload.capability_mode,
            external_impact=payload.external_impact,
            output_text=payload.output_text,
            blocking_reason=payload.blocking_reason,
        )


# Build the production actor used by the live Layer 2 runtime.
def build_default_actor() -> ActorPort:
    return OpenAIActor()


def _load_env_file_values() -> dict[str, str]:
    # Load simple KEY=VALUE pairs from the supported .env file locations.
    env_values: dict[str, str] = {}

    for candidate in _candidate_env_paths():
        if not candidate.exists():
            continue

        for raw_line in candidate.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key:
                env_values[key] = value

    return env_values


def _candidate_env_paths() -> list[Path]:
    # Search both repository root and src-level env files for local development.
    repo_root = Path(__file__).resolve().parents[2]
    return [
        repo_root / ".env",
        repo_root / "src" / ".env",
    ]


__all__ = [
    "ActorExecutionError",
    "OpenAIActor",
    "OpenAIActorSettings",
    "build_actor_system_prompt",
    "build_actor_user_prompt",
    "build_default_actor",
]
