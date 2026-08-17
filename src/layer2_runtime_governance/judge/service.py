from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from environment_config import load_env_file_values
from llm_retry import llm_retry
from .contracts import (
    JudgeAssessment,
    JudgeAssessmentStatus,
    JudgeClaimAssessment,
    JudgeClaimStatus,
    JudgeContext,
    JudgeExecutionError,
    JudgeFinding,
    JudgeFindingCategory,
    JudgeModelMetadata,
    JudgePayloadField,
    JudgePort,
    JudgeRecommendedDirective,
    JudgeSeverity,
    JudgeTaskRequirement,
    JudgeTaskRequirementKind,
    JudgeVerdict,
)
from .prompts import build_judge_system_prompt, build_judge_user_prompt

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled only when the adapter is selected
    OpenAI = None


@dataclass(frozen=True)
class JudgeSettings:
    provider: str = "openai"
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
                file_values.get("LAYER2_JUDGE_PROVIDER", "openai"),
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
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    severity: JudgeSeverity
    task_requirement: "_JudgeTaskRequirementPayload"
    payload_field: JudgePayloadField
    payload_excerpt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="Exact evidence_id values copied from evidence_bundle.allowed_evidence_ids.",
    )


class _JudgePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_assessments: list["_JudgeClaimAssessmentPayload"] = Field(...)
    findings: list[_JudgeFindingPayload] = Field(default_factory=list)
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    rationale: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    verdict: JudgeVerdict
    severity: JudgeSeverity
    confidence: float = Field(..., ge=0.0, le=1.0)
    evaluated_operation_digest: str = Field(
        ...,
        pattern=r"^[0-9a-f]{64}$",
    )
    evaluated_evidence_snapshot_digest: str = Field(
        ...,
        pattern=r"^[0-9a-f]{64}$",
    )
    evaluated_evidence_selection_digest: str = Field(
        ...,
        pattern=r"^[0-9a-f]{64}$",
    )


class _JudgeClaimAssessmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_summary: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1)
    ]
    status: JudgeClaimStatus
    selected_evidence_refs: list[str] = Field(
        default_factory=list,
        description="Exact IDs present in both allowed_evidence_ids and selected_evidence_refs.",
    )
    supporting_evidence_refs: list[str] = Field(
        default_factory=list,
        description="Exact evidence_id values copied from evidence_bundle.allowed_evidence_ids.",
    )
    contradicting_evidence_refs: list[str] = Field(
        default_factory=list,
        description="Exact evidence_id values copied from evidence_bundle.allowed_evidence_ids.",
    )
    explanation: str = Field(default="")


class _JudgeTaskRequirementPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: JudgeTaskRequirementKind
    index: int = Field(..., ge=0)
    quote: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DisabledJudge(JudgePort):
    judge_name = "disabled_judge"
    prompt_version = "external_operation_judge_v11_disabled"

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
    prompt_version = "external_operation_judge_v11"
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
        try:
            _validate_judge_payload(payload=payload, context=context)
        except JudgeExecutionError as protocol_error:
            # One immediate correction keeps schema mistakes local to the Judge adapter.
            try:
                response = self._request_judge_response(
                    context=context,
                    protocol_correction=str(protocol_error),
                )
            except Exception as exc:  # pragma: no cover - provider/network failures
                raise JudgeExecutionError(f"Judge protocol retry failed: {exc}") from exc
            payload = getattr(response, "output_parsed", None)
            if payload is None:
                raise JudgeExecutionError(
                    "Judge protocol retry returned no structured output."
                )
            try:
                _validate_judge_payload(payload=payload, context=context)
            except JudgeExecutionError as retry_error:
                raise JudgeExecutionError(
                    f"Judge protocol retry exhausted: {retry_error}"
                ) from retry_error

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
                task_requirement=JudgeTaskRequirement(
                    kind=item.task_requirement.kind,
                    index=item.task_requirement.index,
                    quote=item.task_requirement.quote,
                ),
                payload_field=item.payload_field,
                payload_excerpt=item.payload_excerpt,
            )
            for item in payload.findings
        )
        claim_assessments = tuple(
            JudgeClaimAssessment(
                claim_summary=" ".join(item.claim_summary.split()),
                status=item.status,
                selected_evidence_refs=tuple(item.selected_evidence_refs),
                supporting_evidence_refs=tuple(item.supporting_evidence_refs),
                contradicting_evidence_refs=tuple(item.contradicting_evidence_refs),
                explanation=" ".join(item.explanation.split()),
            )
            for item in payload.claim_assessments
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
            claim_assessments=claim_assessments,
            evaluated_evidence_snapshot_digest=(
                payload.evaluated_evidence_snapshot_digest
            ),
            evaluated_evidence_selection_digest=(
                payload.evaluated_evidence_selection_digest
            ),
        )

    # Retry only transient provider failures around one typed semantic request.
    @llm_retry
    def _request_judge_response(
        self,
        *,
        context: JudgeContext,
        protocol_correction: str | None = None,
    ) -> object:
        return self._client.responses.parse(
            model=self._settings.model,
            input=[
                {"role": "system", "content": build_judge_system_prompt()},
                {
                    "role": "user",
                    "content": build_judge_user_prompt(
                        context=context,
                        protocol_correction=protocol_correction,
                    ),
                },
            ],
            text_format=_JudgePayload,
        )


# Build the explicitly configured semantic judge adapter.
def build_default_judge() -> JudgePort:
    settings = JudgeSettings.from_env()
    if settings.provider == "openai":
        return OpenAIJudge(settings=settings)
    return DisabledJudge()


# Reject model output that is not bound to the supplied semantic universe.
def _validate_judge_payload(
    *,
    payload: _JudgePayload,
    context: JudgeContext,
) -> None:
    expected_bindings = (
        (
            payload.evaluated_operation_digest,
            context.external_operation.payload_digest,
            "evaluated_operation_digest",
        ),
        (
            payload.evaluated_evidence_snapshot_digest,
            context.evidence_snapshot_digest,
            "evaluated_evidence_snapshot_digest",
        ),
        (
            payload.evaluated_evidence_selection_digest,
            context.evidence_selection_digest,
            "evaluated_evidence_selection_digest",
        ),
    )
    for actual, expected, field_name in expected_bindings:
        if expected is None or actual != expected:
            raise JudgeExecutionError(
                f"Judge {field_name} does not match the supplied context."
            )

    known_evidence_ids = {item.evidence_id for item in context.observations}
    actor_selected_ids = set(context.selected_evidence_refs)
    for finding in payload.findings:
        _validate_finding_anchor(finding=finding, context=context)
        _require_known_evidence_refs(
            refs=finding.evidence_refs,
            known_evidence_ids=known_evidence_ids,
            field_name="finding.evidence_refs",
        )
    if payload.verdict in {JudgeVerdict.NEEDS_REVISION, JudgeVerdict.UNSAFE} and not payload.findings:
        raise JudgeExecutionError(
            "Judge needs_revision or unsafe verdict requires at least one valid finding."
        )
    for claim in payload.claim_assessments:
        _require_known_evidence_refs(
            refs=claim.selected_evidence_refs,
            known_evidence_ids=known_evidence_ids,
            field_name="claim.selected_evidence_refs",
        )
        if not set(claim.selected_evidence_refs).issubset(actor_selected_ids):
            raise JudgeExecutionError(
                "Judge claim selected_evidence_refs contains an observation "
                "that the actor did not select."
            )
        _require_known_evidence_refs(
            refs=claim.supporting_evidence_refs,
            known_evidence_ids=known_evidence_ids,
            field_name="claim.supporting_evidence_refs",
        )
        _require_known_evidence_refs(
            refs=claim.contradicting_evidence_refs,
            known_evidence_ids=known_evidence_ids,
            field_name="claim.contradicting_evidence_refs",
        )


# Verify that each semantic objection names a real task-to-payload conflict.
def _validate_finding_anchor(
    *,
    finding: _JudgeFindingPayload,
    context: JudgeContext,
) -> None:
    requirement_values = {
        JudgeTaskRequirementKind.GOAL: (context.task_goal,),
        JudgeTaskRequirementKind.CONTEXT: (context.authorized_context,),
        JudgeTaskRequirementKind.CONSTRAINT: context.task_constraints,
        JudgeTaskRequirementKind.SUCCESS_CRITERION: context.task_success_criteria,
    }
    requirements = requirement_values[finding.task_requirement.kind]
    index = finding.task_requirement.index
    if index >= len(requirements):
        raise JudgeExecutionError(
            "Judge finding.task_requirement.index is outside the supplied task surface."
        )
    if finding.task_requirement.quote not in requirements[index]:
        raise JudgeExecutionError(
            "Judge finding.task_requirement.quote is not an exact quote from the supplied task element."
        )
    payload_fields = _payload_field_values(context=context)
    field_name = finding.payload_field.value
    if field_name not in payload_fields:
        raise JudgeExecutionError(
            "Judge finding.payload_field is not available on the supplied payload."
        )
    if finding.payload_excerpt not in payload_fields[field_name]:
        raise JudgeExecutionError(
            "Judge finding.payload_excerpt is not an exact excerpt from the supplied payload field."
        )
    if (
        context.operation_stage.value == "communication_draft_review"
        and finding.payload_field
        not in {
            JudgePayloadField.CHANNEL,
            JudgePayloadField.RECIPIENT,
            JudgePayloadField.SUBJECT,
            JudgePayloadField.BODY,
            JudgePayloadField.CC,
            JudgePayloadField.BCC,
        }
    ):
        raise JudgeExecutionError(
            "Judge communication_draft_review finding must anchor a communication payload field."
        )


# Expose only typed payload values; the provider cannot anchor a finding to process prose.
def _payload_field_values(*, context: JudgeContext) -> dict[str, str]:
    raw_payload = asdict(context.external_operation.payload)
    return {
        str(name): _payload_value_to_text(value)
        for name, value in raw_payload.items()
    }


# Compare excerpts against stable text while retaining the exact typed payload fields.
def _payload_value_to_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(_payload_value_to_text(item) for item in value)
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


# Require every model-produced reference to resolve inside the exact JudgeContext.
def _require_known_evidence_refs(
    *,
    refs: list[str],
    known_evidence_ids: set[str],
    field_name: str,
) -> None:
    unknown = sorted(set(refs) - known_evidence_ids)
    if unknown:
        raise JudgeExecutionError(
            f"Judge {field_name} contains unknown observation ids: "
            f"{', '.join(unknown)}."
        )


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
