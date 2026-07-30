from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from llm_retry import llm_retry

from ..domain import PlanStep, PlannerDecision, TaskState
from ..infrastructure.environment import candidate_env_paths, load_env_file_values

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled at runtime if dependency is missing
    OpenAI = None


@dataclass(frozen=True)
class OpenAIPlannerSettings:
    model: str = "gpt-5.4-nano"
    api_key: str | None = None
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "OpenAIPlannerSettings":
        # Merge process env and local .env files into one planner settings object.
        env_file_values = _load_env_file_values()

        return cls(
            model=os.getenv(
                "LAYER2_PLANNER_OPENAI_MODEL",
                env_file_values.get("LAYER2_PLANNER_OPENAI_MODEL", "gpt-5.4-nano"),
            ),
            api_key=os.getenv("OPENAI_API_KEY", env_file_values.get("OPENAI_API_KEY")),
            timeout_seconds=float(
                os.getenv(
                    "LAYER2_PLANNER_OPENAI_TIMEOUT_SECONDS",
                    env_file_values.get("LAYER2_PLANNER_OPENAI_TIMEOUT_SECONDS", "30"),
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
- Derive the complete trajectory from the actual goal, context, constraints, success criteria, available material, and runtime state.
- Identify prerequisites and dependencies before work that relies on them.
- Distinguish information acquisition, transformation, validation, artifact production, and requested external effects when those are materially separate outcomes.
- When completion depends on facts not present in the task state, include semantic work that acquires and observes those facts before relying on them.
- When the task can be completed from existing material, do not add unnecessary acquisition work.
- Cover every success criterion and constraint through one or more concrete plan outcomes.
- Make every step independently understandable, executable, and verifiable by its objective and expected outcome.
- Make the expected outcome a durable result that a later step can use without reconstructing raw observations.
- Choose the number and granularity of steps from task complexity. There is no preferred fixed step count.
- Avoid vague umbrella steps that hide materially different prerequisites or outputs.
- Avoid artificial micro-steps that do not produce a meaningful intermediate state.
- Do not split formatting, checking, and preparation when they produce the same durable result.
- Do not add speculative acquisition work that is not required by the goal or an explicit dependency.
- Preserve requested end results exactly; never replace substantive work with formatting, drafting, or administrative closure.
- Describe what must be achieved, not the capability invocation used to achieve it.
- Respect read_only_mode when it is true.
- Do not include human review as a normal plan step.
- Do not include compliance, legal classification, policy simulation, or governance as a plan step.
- For a task with a governed external effect, the terminal semantic step produces
  the complete execution-ready proposal. Human approval, capability invocation,
  delivery or execution receipts, and runtime completion are governed lifecycle
  transitions, not additional plan steps.
- Do not add speculative retry, delivery-verification, or failure-recovery steps before
  such a failure has actually been observed. Runtime execution and replanning handle
  concrete failures when they occur.
- Do not pre-judge, reject, sanitize, or rewrite a requested external operation
  merely because it may be risky. Preserve the requested semantic objective and
  let the governance node evaluate the resulting proposal.
- Do not add generic administrative steps like "finalize task" unless closure itself is materially part of the user goal.
- If the task still lacks key information, produce the safest semantic next steps you can from the known state.
- Before returning, review the proposed plan once: remove redundant steps, ensure all success criteria are covered, and preserve exactly one semantic representation of any requested external effect.
- Do not explain your reasoning outside the schema.
""".strip()


# Build the user prompt from the canonical runtime state.
def build_planner_user_prompt(*, task_state: TaskState) -> str:
    payload = {
        "task_spec": {
            "goal": task_state.task_spec.goal,
            "context": task_state.task_spec.context,
            "constraints": task_state.task_spec.constraints,
            "sensitivity": task_state.task_spec.sensitivity.value,
            "success_criteria": task_state.task_spec.success_criteria,
            "execution_intent": task_state.task_spec.execution_intent.value,
        },
        "execution_policy": {
            "policy_profile_name": task_state.execution_policy.policy_profile_name,
            "confidence_threshold": task_state.execution_policy.confidence_threshold,
            "read_only_mode": task_state.execution_policy.read_only_mode,
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
    prompt_version = "planner_v4"

    def __init__(self, settings: OpenAIPlannerSettings | None = None) -> None:
        # Validate planner prerequisites once at construction time.
        self._settings = settings or OpenAIPlannerSettings.from_env()

        if OpenAI is None:
            raise RuntimeError(
                "The 'openai' package is not installed. Install it before using the Layer 2 planner."
            )
        if not self._settings.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is missing. Set it in the process environment or in .env."
            )

        self._client = OpenAI(
            api_key=self._settings.api_key,
            timeout=self._settings.timeout_seconds,
        )

    def plan(self, *, task_state: TaskState) -> PlannerDecision:
        # Ask OpenAI for a typed semantic planning payload and convert it into domain models.
        try:
            response = self._request_plan_response(task_state=task_state)
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

    # Retry only transient provider failures so one rate-limit does not abort the run.
    @llm_retry
    def _request_plan_response(self, *, task_state: TaskState) -> object:
        return self._client.responses.parse(
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


# Build the production planner used by the live Layer 2 runtime.
def build_default_planner() -> PlannerPort:
    return OpenAIPlanner()


def _load_env_file_values() -> dict[str, str]:
    return dict(load_env_file_values())


def _candidate_env_paths() -> list[Path]:
    return list(candidate_env_paths())
