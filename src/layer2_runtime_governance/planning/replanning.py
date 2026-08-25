from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from environment_config import load_env_file_values
from ..domain import (
    EvidenceBurden,
    PlanStep,
    PlanStepStatus,
    TaskState,
    derive_evidence_obligation_floor,
    validate_external_effect_plan_contract,
    validate_plan_step_evidence_contract,
    validate_replacement_evidence_obligation,
)
from ..prompt_projection import (
    project_prompt_observation,
    project_prompt_plan_step,
    project_prompt_scratchpad_entry,
)
from .openai_adapter import (
    ParsedOpenAIClient,
    build_parsed_openai_client,
    request_parsed_openai_response,
    require_parsed_output,
)


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
    def from_env(cls) -> OpenAIReplannerSettings:
        # Merge process env and local .env files into one replanner settings object.
        env_file_values = dict(load_env_file_values())

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


class _ReplanEvidenceBurdenPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_independent_sources: int = Field(default=1, ge=1)
    require_retrieved_content: bool = Field(default=True)
    freshness_window_days: int | None = Field(default=None, ge=1)


class _ReplanStepPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(..., min_length=1)
    rationale: str = Field(..., min_length=1)
    expected_outcome: str = Field(..., min_length=1)
    produces_factual_claims: bool = Field(default=False)
    evidence_burden: _ReplanEvidenceBurdenPayload | None = Field(default=None)
    produces_governed_external_effect_proposal: bool

    # Keep replacement steps subject to the same proof-obligation invariant.
    @model_validator(mode="after")
    def validate_factual_evidence_burden(self) -> "_ReplanStepPayload":
        validate_plan_step_evidence_contract(
            produces_factual_claims=self.produces_factual_claims,
            evidence_burden=self.evidence_burden,
        )
        return self


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
- produces_factual_claims (boolean: true if this step produces factual claims that leave the system or will be used for downstream decision-making, false otherwise)
- evidence_burden (required object when produces_factual_claims is true; omit it when produces_factual_claims is false. Fields: min_independent_sources [default 1], require_retrieved_content [default true], freshness_window_days [optional integer])
- produces_governed_external_effect_proposal (boolean: true only for the terminal remaining step that prepares the exact governed external operation requested by an execute-after-governance task)

Rules:
- Classify a step as factual when its durable result selects, validates, extracts,
  compares, or reports source identities, dates, organizations, measurements, claims,
  or other world facts that a later step will use. Evidence acquisition is not
  non-factual merely because the concrete capability call is operational.
- A procedural result derived only from the user-supplied task contract can remain
  non-factual; for example, restating a supplied reference date or calculating its
  requested interval does not require external evidence.
- Preserve the `remaining_evidence_obligation` supplied in the runtime context. The
  replacement trajectory must contain factual work when it is required and must not
  reduce its source-count, retrieved-content, or freshness dimensions.
- Do not modify or repeat completed steps.
- Do not emit concrete tool calls, URLs, query strings, API inputs, shell commands, or executable payloads.
- Stay at the semantic planning level.
- Use completed steps, observed evidence, scratchpad entries, step validations, and the actor blocking reason as context.
- Preserve alignment with the original task, constraints, success criteria, and policy.
- Produce only the remaining steps needed from the current blocked point to completion.
- Make the replacement trajectory complete, specific, proportional to the remaining
  work, and executable by the actor. Do not optimize for a fixed or artificially small
  number of steps.
- Every replacement step must produce a meaningful semantic outcome. Do not create
  standalone bookkeeping, formatting, citation-checklist, quality-scoring, compliance,
  policy-simulation, or human-review meta-steps unless that activity is itself an
  explicit user-requested result.
- Do not introduce evidence taxonomies, universal source grades, claim ceilings, or
  per-claim certification procedures. Use the concrete observations already available
  and plan only the substantive work still required.
- If the actor needs more evidence, plan semantic evidence acquisition, not a concrete tool invocation.
- For a task with a governed external effect, the terminal semantic step produces
  the complete execution-ready proposal. Human approval, capability invocation,
  delivery or execution receipts, and runtime completion are governed lifecycle
  transitions, not replacement plan steps.
- When execution_intent is execute_after_governance, mark exactly that terminal
  remaining step with produces_governed_external_effect_proposal=true and mark every
  earlier replacement step false. When execution_intent is proposal_only, mark every
  replacement step false.
- Do not add speculative retry, delivery-verification, or failure-recovery steps before
  such a failure has actually been observed. Runtime execution handles concrete
  failures and may request another replan from the resulting observable state.
- If runtime_flags includes `extractability_limited`, do not keep replacing the trajectory with equivalent extraction-only steps; choose a materially different recovery path or controlled clarification if the task truly requires unavailable information.
- If the task is no longer safely executable, produce the safest semantic step that leads to clarification or controlled stopping.
- Do not explain your reasoning outside the schema.
""".strip()


def build_replanner_user_prompt(
    *,
    task_state: TaskState,
    contract_feedback: str | None = None,
) -> str:
    # Provide enough runtime context for recovery without letting the replanner execute.
    actor_result = task_state.actor_result
    current_step = task_state.current_plan_step
    current_index = task_state.current_plan_step_index
    remaining_steps = (
        task_state.plan_steps[current_index:] if current_index is not None else []
    )
    evidence_floor = derive_evidence_obligation_floor(remaining_steps)

    payload = {
        "task_spec": {
            "goal": task_state.task_spec.goal,
            "context": task_state.task_spec.context,
            "constraints": task_state.task_spec.constraints,
            "sensitivity": task_state.task_spec.sensitivity.value,
            "success_criteria": task_state.task_spec.success_criteria,
            "execution_intent": task_state.execution_intent.value,
        },
        "execution_policy": {
            "policy_profile_name": task_state.execution_policy.policy_profile_name,
            "confidence_threshold": task_state.execution_policy.confidence_threshold,
            "read_only_mode": task_state.execution_policy.read_only_mode,
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
            project_prompt_plan_step(step=current_step)
            if current_step is not None
            else None
        ),
        "remaining_evidence_obligation": {
            "factual_step_required": evidence_floor is not None,
            "min_independent_sources": (
                evidence_floor.min_independent_sources if evidence_floor else None
            ),
            "require_retrieved_content": (
                evidence_floor.require_retrieved_content if evidence_floor else None
            ),
            "freshness_window_days": (
                evidence_floor.freshness_window_days if evidence_floor else None
            ),
        },
        "prior_contract_feedback": contract_feedback,
        "plan_steps": [
            project_prompt_plan_step(step=step)
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
        "observations": [
            project_prompt_observation(observation=observation)
            for observation in task_state.observations
        ],
        "scratchpad": [
            project_prompt_scratchpad_entry(entry=entry)
            for entry in task_state.scratchpad
        ],
        "step_validations": [
            {
                "plan_step_id": validation.plan_step_id,
                "actor_self_confidence": validation.actor_self_confidence,
                "evidence_refs": list(validation.evidence_refs),
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
    prompt_version = "replanner_v5"

    def __init__(self, settings: OpenAIReplannerSettings | None = None) -> None:
        # Validate replanner prerequisites once at construction time.
        self._settings = settings or OpenAIReplannerSettings.from_env()
        self._client: ParsedOpenAIClient = build_parsed_openai_client(
            api_key=self._settings.api_key,
            timeout_seconds=self._settings.timeout_seconds,
            adapter_label="replanner",
        )

    # Ask OpenAI for a typed replacement trajectory and convert it into domain models.
    def replan(self, *, task_state: TaskState) -> ReplanDecision:
        current_index = task_state.current_plan_step_index
        replaced_steps = (
            task_state.plan_steps[current_index:] if current_index is not None else []
        )
        contract_feedback: str | None = None

        # Give the semantic replanner one bounded correction attempt for contract drift.
        for contract_attempt in range(2):
            try:
                response = self._request_replan_response(
                    task_state=task_state,
                    contract_feedback=contract_feedback,
                )
            except Exception as exc:  # pragma: no cover - network/client errors
                raise ReplannerExecutionError(f"Replanner request failed: {exc}") from exc

            payload = cast(
                _ReplanningPayload,
                require_parsed_output(
                    response=response,
                    error_type=ReplannerExecutionError,
                    missing_output_message="Replanner returned no structured output.",
                ),
            )
            decision = ReplanDecision(
                replacement_steps=[
                    PlanStep(
                        step_id=f"replan_step_{index}",
                        objective=step.objective,
                        rationale=step.rationale,
                        expected_outcome=step.expected_outcome,
                        status=PlanStepStatus.PENDING,
                        produces_factual_claims=step.produces_factual_claims,
                        evidence_burden=(
                            EvidenceBurden(
                                min_independent_sources=(
                                    step.evidence_burden.min_independent_sources
                                ),
                                require_retrieved_content=(
                                    step.evidence_burden.require_retrieved_content
                                ),
                                    freshness_window_days=(
                                        step.evidence_burden.freshness_window_days
                                    ),
                            )
                            if step.evidence_burden is not None
                            else None
                        ),
                        produces_governed_external_effect_proposal=(
                            step.produces_governed_external_effect_proposal
                        ),
                    )
                    for index, step in enumerate(payload.replacement_steps, start=1)
                ],
                rationale=payload.rationale_summary,
                replanner_name=self.replanner_name,
                prompt_version=self.prompt_version,
            )
            try:
                validate_replacement_evidence_obligation(
                    replaced_steps=replaced_steps,
                    replacement_steps=decision.replacement_steps,
                )
                validate_external_effect_plan_contract(
                    steps=decision.replacement_steps,
                    execution_intent=task_state.execution_intent,
                )
            except ValueError as exc:
                if contract_attempt == 0:
                    contract_feedback = str(exc)
                    continue
                raise ReplannerExecutionError(
                    f"Replanner violated the semantic plan contract after correction: {exc}"
                ) from exc
            return decision

        raise ReplannerExecutionError("Replanner exhausted its contract correction attempt.")

    # Delegate typed parsing and transient retries to the shared provider boundary.
    def _request_replan_response(
        self,
        *,
        task_state: TaskState,
        contract_feedback: str | None,
    ) -> object:
        return request_parsed_openai_response(
            client=self._client,
            model=self._settings.model,
            system_prompt=build_replanner_system_prompt(),
            user_prompt=build_replanner_user_prompt(
                task_state=task_state,
                contract_feedback=contract_feedback,
            ),
            text_format=_ReplanningPayload,
        )


class LocalConservativeReplanner(ReplannerPort):
    # This fallback remains useful for tests and offline demos.
    replanner_name = "local_conservative_replanner"
    prompt_version = "replanner_v5"

    # Produce a compact recovery step from the actor blocking reason.
    def replan(self, *, task_state: TaskState) -> ReplanDecision:
        actor_result = task_state.actor_result
        current_step = task_state.current_plan_step
        current_index = task_state.current_plan_step_index

        if actor_result is None or current_step is None or current_index is None:
            raise ValueError("Cannot replan without an actor result and current plan step.")

        reason = actor_result.blocking_reason or actor_result.summary
        # Preserve the original objective in the recovery step so drift is visible in traces.
        replacement_step = PlanStep(
            step_id=f"{current_step.step_id}_replanned",
            objective=f"Recover from the blocked step: {current_step.objective}",
            rationale=f"The actor requested replanning because: {reason}",
            expected_outcome="A revised executable step that lets the runtime continue safely.",
            status=PlanStepStatus.PENDING,
            produces_factual_claims=current_step.produces_factual_claims,
            evidence_burden=current_step.evidence_burden,
            produces_governed_external_effect_proposal=(
                current_step.produces_governed_external_effect_proposal
            ),
        )
        # Retain future semantic work so the offline fallback cannot erase its obligations.
        retained_future_steps = [
            replace(step, status=PlanStepStatus.PENDING)
            for step in task_state.plan_steps[current_index + 1 :]
            if step.status
            in {
                PlanStepStatus.PENDING,
                PlanStepStatus.IN_PROGRESS,
                PlanStepStatus.BLOCKED,
            }
        ]

        return ReplanDecision(
            replacement_steps=[replacement_step, *retained_future_steps],
            rationale=(
                "Replaced the blocked step conservatively and retained the remaining "
                "semantic trajectory."
            ),
            replanner_name=self.replanner_name,
            prompt_version=self.prompt_version,
        )


# Build the default replanner for the current runtime slice.
def build_default_replanner() -> ReplannerPort:
    return OpenAIReplanner()


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
