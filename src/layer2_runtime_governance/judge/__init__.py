from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from llm_retry import llm_retry

from ..actor_contract import ActorResultType
from ..behavioral import BehavioralAssessment, BehavioralRecommendedDirective
from ..infrastructure.environment import candidate_env_paths, load_env_file_values
from ..oversight import (
    DynamicPolicyAssessment,
    DynamicPolicyRecommendedDirective,
    OversightSignalLevel,
    _available_evidence_support_level,
    _output_claim_support_level,
    _output_stays_within_intake_support,
    _precision_mismatch_detected as _runtime_precision_mismatch_detected,
    _required_evidence_support_level,
)
from ..domain import ExternalCommunicationPhase, TaskState
from ..tool_contract import EvidenceSupportLevel

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled at runtime if dependency is missing
    OpenAI = None

if TYPE_CHECKING:
    from ..governance_components import PolicyEvaluationInput, StaticPolicyAssessment


class JudgeRecommendedDirective(str, Enum):
    # Judge recommendations stay advisory and may only increase conservativeness later.
    CONTINUE = "continue"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    REPLAN = "replan"
    WAIT_HUMAN_REVIEW = "wait_human_review"


@dataclass(frozen=True)
class JudgeAssessment:
    # JudgeAssessment records whether the semantic judge ran and what it recommended.
    invoked: bool
    recommendation: JudgeRecommendedDirective
    confidence: float
    summary: str
    rationale: str
    flags: tuple[str, ...]
    evaluated_target: str


@dataclass(frozen=True)
class JudgeSettings:
    # JudgeSettings keep provider selection explicit and testable.
    provider: str = "disabled"
    model: str = "gpt-5.4-nano"
    api_key: str | None = None
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "JudgeSettings":
        # Merge process env and local .env files into one judge settings object.
        env_file_values = _load_env_file_values()
        return cls(
            provider=os.getenv(
                "LAYER2_JUDGE_PROVIDER",
                env_file_values.get("LAYER2_JUDGE_PROVIDER", "disabled"),
            ).strip().lower(),
            model=os.getenv(
                "LAYER2_JUDGE_OPENAI_MODEL",
                env_file_values.get("LAYER2_JUDGE_OPENAI_MODEL", "gpt-5.4-nano"),
            ),
            api_key=os.getenv("OPENAI_API_KEY", env_file_values.get("OPENAI_API_KEY")),
            timeout_seconds=float(
                os.getenv(
                    "LAYER2_JUDGE_OPENAI_TIMEOUT_SECONDS",
                    env_file_values.get("LAYER2_JUDGE_OPENAI_TIMEOUT_SECONDS", "30"),
                )
            ),
        )


class JudgePort(Protocol):
    # JudgePort decouples governance from one concrete semantic judge backend.
    judge_name: str
    prompt_version: str

    def evaluate(
        self,
        *,
        task_state: TaskState,
        policy_input: "PolicyEvaluationInput",
        static_policy_assessment: "StaticPolicyAssessment",
        dynamic_policy_assessment: DynamicPolicyAssessment,
        behavioral_assessment: BehavioralAssessment,
        evaluated_target: str,
        invocation_flags: tuple[str, ...],
    ) -> JudgeAssessment: ...


class JudgeExecutionError(RuntimeError):
    # JudgeExecutionError is raised when the configured judge cannot return a valid assessment.
    pass


class _JudgePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0.0, le=1.0)
    summary: str = Field(..., min_length=1)
    rationale: str = Field(..., min_length=1)
    flags: list[str] = Field(default_factory=list)


class DisabledJudge(JudgePort):
    # DisabledJudge keeps the contract live without forcing a network dependency.
    judge_name = "disabled_judge"
    prompt_version = "judge_v1_disabled"

    def evaluate(
        self,
        *,
        task_state: TaskState,
        policy_input: "PolicyEvaluationInput",
        static_policy_assessment: "StaticPolicyAssessment",
        dynamic_policy_assessment: DynamicPolicyAssessment,
        behavioral_assessment: BehavioralAssessment,
        evaluated_target: str,
        invocation_flags: tuple[str, ...],
    ) -> JudgeAssessment:
        return JudgeAssessment(
            invoked=False,
            recommendation=JudgeRecommendedDirective.CONTINUE,
            confidence=0.0,
            summary="Judge invocation conditions were met, but the judge provider is disabled.",
            rationale=(
                "The runtime evaluated the semantic-judge gate, but LAYER2_JUDGE_PROVIDER is disabled."
            ),
            flags=invocation_flags,
            evaluated_target=evaluated_target,
        )


class OpenAIJudge(JudgePort):
    # OpenAIJudge runs the selective semantic judge through the Responses API.
    judge_name = "openai_layer2_judge"
    prompt_version = "judge_v1"

    def __init__(self, settings: JudgeSettings | None = None) -> None:
        self._settings = settings or JudgeSettings.from_env()
        if OpenAI is None:
            raise RuntimeError(
                "The 'openai' package is not installed. Install it before using the Layer 2 judge."
            )
        if not self._settings.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is missing. Set it in the process environment or in src/.env."
            )
        self._client = OpenAI(
            api_key=self._settings.api_key,
            timeout=self._settings.timeout_seconds,
        )

    # Evaluate the current runtime snapshot and return one typed judge assessment.
    def evaluate(
        self,
        *,
        task_state: TaskState,
        policy_input: "PolicyEvaluationInput",
        static_policy_assessment: "StaticPolicyAssessment",
        dynamic_policy_assessment: DynamicPolicyAssessment,
        behavioral_assessment: BehavioralAssessment,
        evaluated_target: str,
        invocation_flags: tuple[str, ...],
    ) -> JudgeAssessment:
        try:
            response = self._request_judge_response(
                task_state=task_state,
                policy_input=policy_input,
                static_policy_assessment=static_policy_assessment,
                dynamic_policy_assessment=dynamic_policy_assessment,
                behavioral_assessment=behavioral_assessment,
                evaluated_target=evaluated_target,
                invocation_flags=invocation_flags,
            )
        except Exception as exc:  # pragma: no cover - network/client errors
            raise JudgeExecutionError(f"Judge request failed: {exc}") from exc

        payload = getattr(response, "output_parsed", None)
        if payload is None:
            raise JudgeExecutionError("Judge returned no structured output.")

        recommendation = _parse_judge_recommendation(payload.recommendation)
        return JudgeAssessment(
            invoked=True,
            recommendation=recommendation,
            confidence=float(payload.confidence),
            summary=" ".join(payload.summary.split()),
            rationale=" ".join(payload.rationale.split()),
            flags=tuple(_normalize_flag(flag) for flag in payload.flags if _normalize_flag(flag)),
            evaluated_target=evaluated_target,
        )

    # Retry only transient provider failures so selective judge checks stay non-fragile.
    @llm_retry
    def _request_judge_response(
        self,
        *,
        task_state: TaskState,
        policy_input: "PolicyEvaluationInput",
        static_policy_assessment: "StaticPolicyAssessment",
        dynamic_policy_assessment: DynamicPolicyAssessment,
        behavioral_assessment: BehavioralAssessment,
        evaluated_target: str,
        invocation_flags: tuple[str, ...],
    ) -> object:
        return self._client.responses.parse(
            model=self._settings.model,
            input=[
                {
                    "role": "system",
                    "content": build_judge_system_prompt(),
                },
                {
                    "role": "user",
                    "content": build_judge_user_prompt(
                        task_state=task_state,
                        policy_input=policy_input,
                        static_policy_assessment=static_policy_assessment,
                        dynamic_policy_assessment=dynamic_policy_assessment,
                        behavioral_assessment=behavioral_assessment,
                        evaluated_target=evaluated_target,
                        invocation_flags=invocation_flags,
                    ),
                },
            ],
            text_format=_JudgePayload,
        )


# Build the production judge used by the runtime when explicitly enabled.
def build_default_judge() -> JudgePort:
    settings = JudgeSettings.from_env()
    if settings.provider == "openai":
        return OpenAIJudge(settings=settings)
    return DisabledJudge()


# Return whether the semantic judge should run for the current governance pass.
def should_invoke_judge(
    *,
    task_state: TaskState,
    policy_input: "PolicyEvaluationInput",
    static_policy_assessment: "StaticPolicyAssessment",
    dynamic_policy_assessment: DynamicPolicyAssessment,
    behavioral_assessment: BehavioralAssessment,
) -> bool:
    return _resolve_judge_invocation_context(
        task_state=task_state,
        policy_input=policy_input,
        static_policy_assessment=static_policy_assessment,
        dynamic_policy_assessment=dynamic_policy_assessment,
        behavioral_assessment=behavioral_assessment,
    )[0]


# Evaluate the semantic judge only when the selective invocation gate opens.
def evaluate_judge(
    *,
    task_state: TaskState,
    policy_input: "PolicyEvaluationInput",
    static_policy_assessment: "StaticPolicyAssessment",
    dynamic_policy_assessment: DynamicPolicyAssessment,
    behavioral_assessment: BehavioralAssessment,
    judge_service: JudgePort | None = None,
) -> JudgeAssessment:
    should_invoke, evaluated_target, invocation_flags = _resolve_judge_invocation_context(
        task_state=task_state,
        policy_input=policy_input,
        static_policy_assessment=static_policy_assessment,
        dynamic_policy_assessment=dynamic_policy_assessment,
        behavioral_assessment=behavioral_assessment,
    )
    if not should_invoke:
        return JudgeAssessment(
            invoked=False,
            recommendation=JudgeRecommendedDirective.CONTINUE,
            confidence=0.0,
            summary="Judge not invoked for this governance pass.",
            rationale="Static, dynamic, and behavioural signals did not require semantic review.",
            flags=invocation_flags,
            evaluated_target=evaluated_target,
        )

    resolved_judge = judge_service or build_default_judge()
    return resolved_judge.evaluate(
        task_state=task_state,
        policy_input=policy_input,
        static_policy_assessment=static_policy_assessment,
        dynamic_policy_assessment=dynamic_policy_assessment,
        behavioral_assessment=behavioral_assessment,
        evaluated_target=evaluated_target,
        invocation_flags=invocation_flags,
    )


# Build the constrained system prompt for the semantic governance judge.
def build_judge_system_prompt() -> str:
    return """
You are the selective semantic judge for layer 2 governance.
You are not the main planner or actor.
You do not decide hard policy constraints.

Return a structured JSON object with:
- recommendation
- confidence
- summary
- rationale
- flags

Allowed recommendation values:
- continue
- request_more_evidence
- replan
- wait_human_review

Rules:
- Evaluate overclaiming, semantic drift, fragile completion, goal/evidence mismatch, and risky external drafts.
- Be conservative when the output precision exceeds the support level of the observed evidence.
- Do not invent hard compliance rules or tool restrictions.
- Do not return a more permissive recommendation than the static policy already allows.
- Keep summary and rationale concise, explicit, and audit-friendly.
- flags must be short snake_case strings.
""".strip()


# Build the user payload from the current runtime state and layered governance outputs.
def build_judge_user_prompt(
    *,
    task_state: TaskState,
    policy_input: "PolicyEvaluationInput",
    static_policy_assessment: "StaticPolicyAssessment",
    dynamic_policy_assessment: DynamicPolicyAssessment,
    behavioral_assessment: BehavioralAssessment,
    evaluated_target: str,
    invocation_flags: tuple[str, ...],
) -> str:
    current_step = task_state.current_plan_step
    actor_result = task_state.actor_result
    step_validation = actor_result.step_validation if actor_result is not None else None
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
        "current_plan_step": (
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
        "actor_result": (
            {
                "plan_step_id": actor_result.plan_step_id,
                "result_type": actor_result.result_type.value,
                "summary": actor_result.summary,
                "capability_mode": actor_result.capability_mode.value,
                "external_impact": actor_result.external_impact,
                "output_text": actor_result.output_text,
                "blocking_reason": actor_result.blocking_reason,
            }
            if actor_result is not None
            else None
        ),
        "step_validation": (
            {
                "plan_step_id": step_validation.plan_step_id,
                "objective_satisfied": step_validation.objective_satisfied,
                "evidence_sufficient": step_validation.evidence_sufficient,
                "confidence": step_validation.confidence,
                "next_action": step_validation.next_action.value,
                "rationale_summary": step_validation.rationale_summary,
                "residual_uncertainty": step_validation.residual_uncertainty,
            }
            if step_validation is not None
            else None
        ),
        "observed_evidence": [
            {
                "evidence_id": item.evidence_id,
                "plan_step_id": item.plan_step_id,
                "title": item.title,
                "locator": item.locator,
                "excerpt": item.excerpt,
                "source_kind": item.source_kind.value,
                "source_tool": item.source_tool.value if item.source_tool is not None else None,
                "support_level": item.support_level.value,
            }
            for item in task_state.observed_evidence
        ],
        "static_policy": {
            "decision": static_policy_assessment.policy_decision.value,
            "directive": static_policy_assessment.policy_directive.value,
            "summary": static_policy_assessment.summary,
        },
        "dynamic_policy": {
            "recommended_directive": dynamic_policy_assessment.recommended_directive.value,
            "summary": dynamic_policy_assessment.summary,
            "evidence_quality_level": dynamic_policy_assessment.evidence_quality.level.value,
            "evidence_quality_score": dynamic_policy_assessment.evidence_quality.score,
            "operational_risk_level": dynamic_policy_assessment.operational_risk.level.value,
            "trajectory_health_level": dynamic_policy_assessment.trajectory_health.level.value,
        },
        "behavioral": {
            "recommended_directive": behavioral_assessment.behavioral_recommended_directive.value,
            "summary": behavioral_assessment.summary,
            "matched_run_count": behavioral_assessment.matched_run_count,
            "historical_similarity_score": behavioral_assessment.historical_similarity_score,
            "behavior_deviation_score": behavioral_assessment.behavior_deviation_score,
            "novelty_score": behavioral_assessment.novelty_score,
            "behavioral_flags": behavioral_assessment.behavioral_flags,
        },
        "external_communication": (
            {
                "phase": task_state.external_communication.phase.value,
                "last_transition_summary": task_state.external_communication.last_transition_summary,
            }
            if task_state.external_communication is not None
            else None
        ),
        "policy_input": {
            "current_plan_step_id": policy_input.current_plan_step_id,
            "current_plan_step_objective": policy_input.current_plan_step_objective,
            "actor_result_type": policy_input.actor_result_type.value,
            "external_communication_phase": (
                policy_input.external_communication_phase.value
                if policy_input.external_communication_phase is not None
                else None
            ),
        },
        "judge_invocation": {
            "evaluated_target": evaluated_target,
            "flags": list(invocation_flags),
            "available_support_level": _available_evidence_support_level(task_state=task_state).value,
            "required_support_level": _required_evidence_support_level(task_state=task_state).value,
            "output_claim_support_level": _output_claim_support_level(task_state=task_state).value,
            "precision_mismatch_detected": _precision_mismatch_detected(task_state=task_state),
        },
    }
    return (
        "Evaluate whether this governance pass needs semantic escalation.\n\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}"
    )


# Resolve selective judge invocation from the current layered governance context.
def _resolve_judge_invocation_context(
    *,
    task_state: TaskState,
    policy_input: "PolicyEvaluationInput",
    static_policy_assessment: "StaticPolicyAssessment",
    dynamic_policy_assessment: DynamicPolicyAssessment,
    behavioral_assessment: BehavioralAssessment,
) -> tuple[bool, str, tuple[str, ...]]:
    static_decision = static_policy_assessment.policy_decision.value
    if static_decision in {"deny", "require_human_review", "ask_clarification"}:
        return False, "static_policy_terminal", ("static_policy_terminal",)

    flags: list[str] = []
    target = "actor_output"

    if dynamic_policy_assessment.recommended_directive in {
        DynamicPolicyRecommendedDirective.REQUEST_MORE_EVIDENCE,
        DynamicPolicyRecommendedDirective.REPLAN,
        DynamicPolicyRecommendedDirective.WAIT_HUMAN_REVIEW,
    }:
        flags.append(f"dynamic_{dynamic_policy_assessment.recommended_directive.value}")
        target = "dynamic_escalation"

    if behavioral_assessment.behavioral_recommended_directive != BehavioralRecommendedDirective.CONTINUE:
        flags.append(f"behavioral_{behavioral_assessment.behavioral_recommended_directive.value}")
        target = "behavioral_escalation"

    if task_state.external_communication is not None and (
        task_state.external_communication.phase == ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        or (
            task_state.external_communication.phase == ExternalCommunicationPhase.DRAFT_PROPOSAL
            and task_state.external_communication.review_ready
        )
    ):
        flags.append("external_draft_review")
        target = "external_draft"

    if _precision_mismatch_detected(task_state=task_state):
        flags.append("precision_support_mismatch")
        if target == "actor_output":
            target = "precision_mismatch"

    if _borderline_completion_detected(task_state=task_state):
        flags.append("borderline_completion")
        if target == "actor_output":
            target = "fragile_completion"

    if (
        policy_input.actor_result_type in {ActorResultType.INTERMEDIATE_OUTPUT, ActorResultType.FINAL_OUTPUT}
        and _available_evidence_support_level(task_state=task_state) == EvidenceSupportLevel.SOURCE_DISCOVERY
    ):
        flags.append("output_on_source_discovery_only")
        if target == "actor_output":
            target = "fragile_completion"

    normalized_flags = tuple(dict.fromkeys(flags))
    return bool(normalized_flags), target, normalized_flags


# Detect when the current output claims more than the available evidence safely supports.
def _precision_mismatch_detected(*, task_state: TaskState) -> bool:
    return _runtime_precision_mismatch_detected(task_state=task_state)


# Detect fragile completion attempts that should receive semantic review attention.
def _borderline_completion_detected(*, task_state: TaskState) -> bool:
    actor_result = task_state.actor_result
    if actor_result is None:
        return False
    if actor_result.result_type not in {
        ActorResultType.INTERMEDIATE_OUTPUT,
        ActorResultType.FINAL_OUTPUT,
    }:
        return False

    validation = actor_result.step_validation
    if validation is None:
        return True
    if _output_stays_within_intake_support(task_state=task_state):
        return False
    if not validation.objective_satisfied or not validation.evidence_sufficient:
        return True
    if validation.confidence < task_state.execution_policy.confidence_threshold:
        return True

    available_level = _available_evidence_support_level(task_state=task_state)
    return available_level in {
        EvidenceSupportLevel.LOCAL_ONLY,
        EvidenceSupportLevel.SOURCE_DISCOVERY,
        EvidenceSupportLevel.HIGH_LEVEL_SUMMARY,
    }


# Parse and validate the structured recommendation returned by the judge.
def _parse_judge_recommendation(value: str) -> JudgeRecommendedDirective:
    normalized = " ".join(value.split()).strip().lower()
    try:
        return JudgeRecommendedDirective(normalized)
    except ValueError as exc:
        raise JudgeExecutionError(f"Judge returned an unsupported recommendation: {value}") from exc


# Normalize flag text so persisted traces stay stable.
def _normalize_flag(value: str) -> str:
    return "_".join(part for part in value.strip().lower().split() if part)


# Load simple KEY=VALUE pairs from the supported .env file locations.
def _load_env_file_values() -> dict[str, str]:
    return dict(load_env_file_values())


# Search both repository root and src-level env files for local development.
def _candidate_env_paths() -> list[Path]:
    return list(candidate_env_paths())


__all__ = [
    "DisabledJudge",
    "JudgeAssessment",
    "JudgeExecutionError",
    "JudgePort",
    "JudgeRecommendedDirective",
    "JudgeSettings",
    "OpenAIJudge",
    "build_default_judge",
    "build_judge_system_prompt",
    "build_judge_user_prompt",
    "evaluate_judge",
    "should_invoke_judge",
]
