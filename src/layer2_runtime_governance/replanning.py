from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from .state import PlanStep, PlanStepStatus, TaskState

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled at runtime if dependency is missing
    OpenAI = None


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


@dataclass(frozen=True)
class OpenAIReplannerSettings:
    model: str = "gpt-5.4-nano"
    api_key: str | None = None
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "OpenAIReplannerSettings":
        # Merge process env and local .env files into one replanner settings object.
        env_file_values = _load_env_file_values()

        return cls(
            model=os.getenv(
                "LAYER2_REPLANNER_OPENAI_MODEL",
                env_file_values.get("LAYER2_REPLANNER_OPENAI_MODEL", "gpt-5.4-nano"),
            ),
            api_key=os.getenv("OPENAI_API_KEY", env_file_values.get("OPENAI_API_KEY")),
            timeout_seconds=float(
                os.getenv(
                    "LAYER2_REPLANNER_OPENAI_TIMEOUT_SECONDS",
                    env_file_values.get("LAYER2_REPLANNER_OPENAI_TIMEOUT_SECONDS", "30"),
                )
            ),
        )


class ReplannerExecutionError(RuntimeError):
    """Raised when the replanner cannot produce a valid replan decision."""


class _ReplanStepPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(..., min_length=1)
    rationale: str = Field(..., min_length=1)
    expected_outcome: str = Field(..., min_length=1)


class _ReplanningPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale_summary: str = Field(..., min_length=1)
    replacement_steps: list[_ReplanStepPayload] = Field(..., min_length=1)


def build_replanner_system_prompt() -> str:
    # Keep replanning bounded to semantic recovery, not operational execution.
    return """
You are the replanner for layer 2 of a governed autonomous agent runtime.
The actor requested replanning because the current semantic trajectory is blocked, insufficient, or misaligned.
Your task is to replace only the current and pending trajectory with a better semantic trajectory.

Return a structured object with:
- rationale_summary
- replacement_steps

Each replacement step must contain:
- objective
- rationale
- expected_outcome

Rules:
- Do not modify or repeat completed steps.
- Do not emit concrete tool calls, URLs, query strings, API inputs, shell commands, or executable payloads.
- Stay at the semantic planning level.
- Use completed steps, observed evidence, scratchpad entries, step validations, and the actor blocking reason as context.
- Preserve alignment with the original task, constraints, success criteria, and policy.
- Produce only the remaining steps needed from the current blocked point to completion.
- Keep the replacement trajectory compact and executable by the actor.
- If the actor needs more evidence, plan semantic evidence acquisition, not a concrete tool invocation.
- If runtime_flags includes `extractability_limited`, do not keep replacing the trajectory with more extraction-only steps; pivot to a compact source-backed summary path or a controlled stop/clarification if the task truly requires unavailable precision.
- If the task is no longer safely executable, produce the safest semantic step that leads to clarification or controlled stopping.
- Do not explain your reasoning outside the schema.
""".strip()


def build_replanner_user_prompt(*, task_state: TaskState) -> str:
    # Provide enough runtime context for recovery without letting the replanner execute.
    actor_result = task_state.actor_result
    current_step = task_state.current_plan_step

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
            "budget": {
                "max_steps": task_state.execution_policy.budget.max_steps,
                "max_tool_calls": task_state.execution_policy.budget.max_tool_calls,
            },
        },
        "runtime": {
            "current_state": task_state.current_state.value,
            "runtime_flags": [flag.value for flag in task_state.runtime_flags],
            "tool_call_count": task_state.tool_call_count,
            "actor_turn_count": task_state.actor_turn_count,
        },
        "actor_replan_request": (
            {
                "result_type": actor_result.result_type.value,
                "summary": actor_result.summary,
                "blocking_reason": actor_result.blocking_reason,
            }
            if actor_result is not None
            else None
        ),
        "current_blocked_step": (
            {
                "step_id": current_step.step_id,
                "objective": current_step.objective,
                "rationale": current_step.rationale,
                "expected_outcome": current_step.expected_outcome,
                "status": current_step.status.value,
            }
            if current_step is not None
            else None
        ),
        "plan_steps": [
            {
                "step_id": step.step_id,
                "objective": step.objective,
                "rationale": step.rationale,
                "expected_outcome": step.expected_outcome,
                "status": step.status.value,
            }
            for step in task_state.plan_steps
        ],
        "executed_steps": [
            {
                "step_id": step.step_id,
                "objective": step.objective,
                "result_type": step.result_type.value,
                "summary": step.summary,
                "output_text": step.output_text,
            }
            for step in task_state.executed_steps
        ],
        "observed_evidence": [
            {
                "evidence_id": evidence.evidence_id,
                "plan_step_id": evidence.plan_step_id,
                "title": evidence.title,
                "locator": evidence.locator,
                "excerpt": evidence.excerpt,
                "source_kind": evidence.source_kind.value,
                "source_tool": evidence.source_tool.value if evidence.source_tool is not None else None,
                "support_level": evidence.support_level.value,
            }
            for evidence in task_state.observed_evidence
        ],
        "scratchpad": [
            {
                "turn_index": entry.turn_index,
                "plan_step_id": entry.plan_step_id,
                "entry_type": entry.entry_type.value,
                "summary": entry.summary,
                "evidence_refs": entry.evidence_refs,
            }
            for entry in task_state.scratchpad
        ],
        "step_validations": [
            {
                "plan_step_id": validation.plan_step_id,
                "objective_satisfied": validation.objective_satisfied,
                "evidence_sufficient": validation.evidence_sufficient,
                "confidence": validation.confidence,
                "next_action": validation.next_action.value,
                "rationale_summary": validation.rationale_summary,
                "residual_uncertainty": validation.residual_uncertainty,
            }
            for validation in task_state.step_validations
        ],
    }

    return (
        "Replace the current and pending semantic trajectory from this runtime state.\n\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}"
    )


class OpenAIReplanner(ReplannerPort):
    replanner_name = "openai_layer2_replanner"
    prompt_version = "replanner_v1"

    def __init__(self, settings: OpenAIReplannerSettings | None = None) -> None:
        # Validate replanner prerequisites once at construction time.
        self._settings = settings or OpenAIReplannerSettings.from_env()

        if OpenAI is None:
            raise RuntimeError(
                "The 'openai' package is not installed. Install it before using the Layer 2 replanner."
            )
        if not self._settings.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is missing. Set it in the process environment or in src/.env."
            )

        self._client = OpenAI(
            api_key=self._settings.api_key,
            timeout=self._settings.timeout_seconds,
        )

    # Ask OpenAI for a typed replacement trajectory and convert it into domain models.
    def replan(self, *, task_state: TaskState) -> ReplanDecision:
        try:
            response = self._client.responses.parse(
                model=self._settings.model,
                input=[
                    {
                        "role": "system",
                        "content": build_replanner_system_prompt(),
                    },
                    {
                        "role": "user",
                        "content": build_replanner_user_prompt(task_state=task_state),
                    },
                ],
                text_format=_ReplanningPayload,
            )
        except Exception as exc:  # pragma: no cover - network/client errors
            raise ReplannerExecutionError(f"Replanner request failed: {exc}") from exc

        payload = getattr(response, "output_parsed", None)
        if payload is None:
            raise ReplannerExecutionError("Replanner returned no structured output.")

        return ReplanDecision(
            replacement_steps=[
                PlanStep(
                    step_id=f"replan_step_{index}",
                    objective=step.objective,
                    rationale=step.rationale,
                    expected_outcome=step.expected_outcome,
                    status=PlanStepStatus.PENDING,
                )
                for index, step in enumerate(payload.replacement_steps, start=1)
            ],
            rationale=payload.rationale_summary,
            replanner_name=self.replanner_name,
            prompt_version=self.prompt_version,
        )


class LocalConservativeReplanner(ReplannerPort):
    # This fallback remains useful for tests and offline demos.
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
    return OpenAIReplanner()


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
    "LocalConservativeReplanner",
    "OpenAIReplanner",
    "OpenAIReplannerSettings",
    "ReplanDecision",
    "ReplannerExecutionError",
    "ReplannerPort",
    "build_default_replanner",
    "build_replanner_system_prompt",
    "build_replanner_user_prompt",
]
