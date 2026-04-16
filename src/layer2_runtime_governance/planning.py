from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from .state import PlanStep, PlannerDecision, TaskState

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled at runtime if dependency is missing
    OpenAI = None


@dataclass(frozen=True)
class OpenAIPlannerSettings:
    model: str = "gpt-5.4-mini"
    api_key: str | None = None
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "OpenAIPlannerSettings":
        # Merge process env and local .env files into one planner settings object.
        env_file_values = _load_env_file_values()

        return cls(
            model=os.getenv(
                "LAYER2_PLANNER_OPENAI_MODEL",
                env_file_values.get("LAYER2_PLANNER_OPENAI_MODEL"),
            ),
            api_key=os.getenv("OPENAI_API_KEY", env_file_values.get("OPENAI_API_KEY")),
            timeout_seconds=float(
                os.getenv(
                    "LAYER2_PLANNER_OPENAI_TIMEOUT_SECONDS",
                    env_file_values.get("LAYER2_PLANNER_OPENAI_TIMEOUT_SECONDS"),
                )
            ),
        )


class PlannerPort(Protocol):
    # The planner port keeps the graph decoupled from the concrete LLM implementation.
    planner_name: str
    prompt_version: str

    def plan(self, *, task_state: TaskState) -> PlannerDecision: ...


class PlannerExecutionError(RuntimeError):
    """Raised when the planner cannot produce a valid planning decision."""


class _PlanStepPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(..., min_length=1)
    rationale: str = Field(..., min_length=1)
    expected_outcome: str = Field(..., min_length=1)


class _PlanningPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale_summary: str = Field(..., min_length=1)
    plan_steps: list[_PlanStepPayload] = Field(..., min_length=1)


# Build the constrained system prompt for Layer 2 semantic planning.
def build_planner_system_prompt() -> str:
    return """
You are the planner for layer 2 of a governed agent runtime.
Your task is to produce a complete semantic plan for the task, covering the full intended trajectory from the current state to task completion.

Return a structured object with:
- rationale_summary
- plan_steps

Each item in plan_steps must contain:
- objective
- rationale
- expected_outcome

Rules:
- Stay at the semantic planning level.
- Do not emit executable tool calls.
- Do not mention concrete tool names, URLs, function names, query strings, API inputs, shell commands, or payloads.
- Describe what should be achieved next, not how to invoke external capabilities.
- Produce all materially relevant semantic steps needed to complete the task.
- Keep the plan concise, but complete.
- Keep steps concise and operationally meaningful.
- For research or analysis tasks, prefer evidence collection before synthesis.
- Respect read_only_mode when it is true.
- Do not include human review as a normal plan step.
- Do not add generic administrative steps like "finalize task" unless closure itself is materially part of the user goal.
- If the task still lacks key information, produce the safest semantic next steps you can from the known state.
- Do not explain your reasoning outside the schema.
""".strip()


# Build the user prompt from the canonical runtime state.
def build_planner_user_prompt(*, task_state: TaskState) -> str:
    payload = {
        "task_spec": {
            "goal": task_state.task_spec.goal,
            "context": task_state.task_spec.context,
            "constraints": task_state.task_spec.constraints,
            "domain_type": task_state.task_spec.domain_type.value,
            "domain_label": task_state.task_spec.domain_label,
            "sensitivity": task_state.task_spec.sensitivity.value,
            "success_criteria": task_state.task_spec.success_criteria,
        },
        "execution_policy": {
            "policy_profile_name": task_state.execution_policy.policy_profile_name,
            "confidence_threshold": task_state.execution_policy.confidence_threshold,
            "read_only_mode": task_state.execution_policy.read_only_mode,
            "allowed_tools": task_state.execution_policy.allowed_tools,
            "disallowed_tools": task_state.execution_policy.disallowed_tools,
            "budget": {
                "max_steps": task_state.execution_policy.budget.max_steps,
                "max_tool_calls": task_state.execution_policy.budget.max_tool_calls,
            },
        },
        "intake_metadata": {
            "parse_status": task_state.intake_metadata.parse_status.value,
            "warnings": task_state.intake_metadata.warnings,
            "missing_information": task_state.intake_metadata.missing_information,
        },
        "runtime": {
            "current_state": task_state.current_state.value,
            "runtime_flags": [flag.value for flag in task_state.runtime_flags],
            "step_index": task_state.step_index,
        },
    }

    return (
        "Produce the complete semantic plan for this task from the current state to completion.\n\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}"
    )


class OpenAIPlanner(PlannerPort):
    planner_name = "openai_layer2_planner"
    prompt_version = "planner_v1"

    def __init__(self, settings: OpenAIPlannerSettings | None = None) -> None:
        # Validate planner prerequisites once at construction time.
        self._settings = settings or OpenAIPlannerSettings.from_env()

        if OpenAI is None:
            raise RuntimeError(
                "The 'openai' package is not installed. Install it before using the Layer 2 planner."
            )
        if not self._settings.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is missing. Set it in the process environment or in src/.env."
            )

        self._client = OpenAI(
            api_key=self._settings.api_key,
            timeout=self._settings.timeout_seconds,
        )

    def plan(self, *, task_state: TaskState) -> PlannerDecision:
        # Ask OpenAI for a typed semantic planning payload and convert it into domain models.
        try:
            response = self._client.responses.parse(
                model=self._settings.model,
                input=[
                    {
                        "role": "system",
                        "content": build_planner_system_prompt(),
                    },
                    {
                        "role": "user",
                        "content": build_planner_user_prompt(task_state=task_state),
                    },
                ],
                text_format=_PlanningPayload,
            )
        except Exception as exc:  # pragma: no cover - network/client errors
            raise PlannerExecutionError(f"Planner request failed: {exc}") from exc

        payload = getattr(response, "output_parsed", None)
        if payload is None:
            raise PlannerExecutionError("Planner returned no structured output.")

        plan_steps = [
            PlanStep(
                step_id=f"step_{index}",
                objective=item.objective,
                rationale=item.rationale,
                expected_outcome=item.expected_outcome,
            )
            for index, item in enumerate(payload.plan_steps, start=1)
        ]

        return PlannerDecision(
            plan_steps=plan_steps,
            rationale=payload.rationale_summary,
            planner_name=self.planner_name,
            prompt_version=self.prompt_version,
        )


# Build the production planner used by the live Layer 2 runtime.
def build_default_planner() -> PlannerPort:
    return OpenAIPlanner()


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
