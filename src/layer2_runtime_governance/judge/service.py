from __future__ import annotations

import os
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from llm_retry import llm_retry

from ..infrastructure.environment import load_env_file_values
from .contracts import (
    JudgeAssessment,
    JudgeAssessmentStatus,
    JudgeContext,
    JudgeExecutionError,
    JudgeFinding,
    JudgeFindingCategory,
    JudgeModelMetadata,
    JudgePort,
    JudgeRecommendedDirective,
    JudgeSeverity,
    JudgeVerdict,
)
from .prompts import build_judge_system_prompt, build_judge_user_prompt

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled only when the adapter is selected
    OpenAI = None


@dataclass(frozen=True)
class JudgeSettings:
    provider: str = "disabled"
    model: str = "gpt-5.4-nano"
    api_key: str | None = None
    timeout_seconds: float = 30.0

    # Load provider selection without constructing network clients in the domain.
    @classmethod
    def from_env(cls) -> "JudgeSettings":
        file_values = dict(load_env_file_values())
        return cls(
            provider=os.getenv(
                "LAYER2_JUDGE_PROVIDER",
                file_values.get("LAYER2_JUDGE_PROVIDER", "disabled"),
            ).strip().lower(),
            model=os.getenv(
                "LAYER2_JUDGE_OPENAI_MODEL",
                file_values.get("LAYER2_JUDGE_OPENAI_MODEL", "gpt-5.4-nano"),
            ),
            api_key=os.getenv("OPENAI_API_KEY", file_values.get("OPENAI_API_KEY")),
            timeout_seconds=float(
                os.getenv(
                    "LAYER2_JUDGE_OPENAI_TIMEOUT_SECONDS",
                    file_values.get("LAYER2_JUDGE_OPENAI_TIMEOUT_SECONDS", "30"),
                )
            ),
        )


class _JudgeFindingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: JudgeFindingCategory
    code: str = Field(..., min_length=1)
    severity: JudgeSeverity
    summary: str = Field(..., min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


class _JudgePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: JudgeVerdict
    severity: JudgeSeverity
    confidence: float = Field(..., ge=0.0, le=1.0)
    findings: list[_JudgeFindingPayload] = Field(default_factory=list)
    summary: str = Field(..., min_length=1)
    rationale: str = Field(..., min_length=1)
    evaluated_operation_digest: str = Field(
        ...,
        pattern=r"^[0-9a-f]{64}$",
    )


class DisabledJudge(JudgePort):
    judge_name = "disabled_judge"
    prompt_version = "external_operation_judge_v5_disabled"

    # Required disabled invocations are explicitly unavailable, never safe.
    def evaluate(self, *, context: JudgeContext) -> JudgeAssessment:
        return JudgeAssessment(
            invoked=False,
            recommendation=JudgeRecommendedDirective.WAIT_HUMAN_REVIEW,
            confidence=0.0,
            summary="Semantic judge provider is disabled.",
            rationale="The required governed-external assessment could not run.",
            flags=("judge_unavailable",),
            evaluated_target=context.external_operation.operation_id,
            status=JudgeAssessmentStatus.UNAVAILABLE,
            verdict=JudgeVerdict.UNCERTAIN,
            severity=JudgeSeverity.HIGH,
            evaluated_operation_digest=None,
            prompt_version=self.prompt_version,
            model_metadata=JudgeModelMetadata(
                provider="disabled",
                model="disabled",
                judge_name=self.judge_name,
            ),
        )


class OpenAIJudge(JudgePort):
    judge_name = "openai_layer2_external_operation_judge"
    prompt_version = "external_operation_judge_v5"

    def __init__(self, settings: JudgeSettings | None = None) -> None:
        self._settings = settings or JudgeSettings.from_env()
        if OpenAI is None:
            raise RuntimeError("The 'openai' package is required by the selected judge adapter.")
        if not self._settings.api_key:
            raise RuntimeError("OPENAI_API_KEY is missing for the selected judge adapter.")
        self._client = OpenAI(
            api_key=self._settings.api_key,
            timeout=self._settings.timeout_seconds,
        )

    # Evaluate the exact proposal and reject digest drift as invalid provider output.
    def evaluate(self, *, context: JudgeContext) -> JudgeAssessment:
        try:
            response = self._request_judge_response(context=context)
        except Exception as exc:  # pragma: no cover - provider/network failures
            raise JudgeExecutionError(f"Judge request failed: {exc}") from exc
        payload = getattr(response, "output_parsed", None)
        if payload is None:
            raise JudgeExecutionError("Judge returned no structured output.")
        if payload.evaluated_operation_digest != context.external_operation.payload_digest:
            raise JudgeExecutionError("Judge evaluated_operation_digest does not match the proposal.")

        verdict = payload.verdict
        severity = payload.severity
        findings = tuple(
            JudgeFinding(
                category=item.category,
                code=_normalize_code(item.code),
                severity=item.severity,
                summary=" ".join(item.summary.split()),
                evidence_refs=tuple(
                    " ".join(reference.split()) for reference in item.evidence_refs if reference.strip()
                ),
            )
            for item in payload.findings
        )
        return JudgeAssessment(
            invoked=True,
            recommendation=_legacy_recommendation(verdict),
            confidence=float(payload.confidence),
            summary=" ".join(payload.summary.split()),
            rationale=" ".join(payload.rationale.split()),
            flags=tuple(item.code for item in findings),
            evaluated_target=context.external_operation.operation_id,
            status=JudgeAssessmentStatus.ACTIVE,
            verdict=verdict,
            severity=severity,
            findings=findings,
            evaluated_operation_digest=payload.evaluated_operation_digest,
            prompt_version=self.prompt_version,
            model_metadata=JudgeModelMetadata(
                provider="openai",
                model=self._settings.model,
                judge_name=self.judge_name,
            ),
        )

    # Retry only transient provider failures around one typed semantic request.
    @llm_retry
    def _request_judge_response(self, *, context: JudgeContext) -> object:
        return self._client.responses.parse(
            model=self._settings.model,
            input=[
                {"role": "system", "content": build_judge_system_prompt()},
                {"role": "user", "content": build_judge_user_prompt(context=context)},
            ],
            text_format=_JudgePayload,
        )


# Build the explicitly configured semantic judge adapter.
def build_default_judge() -> JudgePort:
    settings = JudgeSettings.from_env()
    if settings.provider == "openai":
        return OpenAIJudge(settings=settings)
    return DisabledJudge()


# Keep finding codes stable for traces and policy.
def _normalize_code(value: str) -> str:
    return "_".join(value.strip().lower().split())


# Preserve the old read-only field without making it a decision input.
def _legacy_recommendation(verdict: JudgeVerdict) -> JudgeRecommendedDirective:
    return {
        JudgeVerdict.SAFE: JudgeRecommendedDirective.CONTINUE,
        JudgeVerdict.NEEDS_REVISION: JudgeRecommendedDirective.REQUEST_MORE_EVIDENCE,
        JudgeVerdict.UNSAFE: JudgeRecommendedDirective.WAIT_HUMAN_REVIEW,
        JudgeVerdict.UNCERTAIN: JudgeRecommendedDirective.WAIT_HUMAN_REVIEW,
    }[verdict]


__all__ = [
    "DisabledJudge",
    "JudgeSettings",
    "OpenAIJudge",
    "build_default_judge",
]
