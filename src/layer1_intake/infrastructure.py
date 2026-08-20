from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from environment_config import load_env_file_values
from llm_retry import llm_retry

from .models import (
    ExecutionIntent,
    InvalidTaskSpecError,
    SensitivityLevel,
    TaskSpec,
)
from .service import (
    ExecutionPolicyRepositoryPort,
    InputFingerprintPort,
    IntakeResultValidator,
    IntakeService,
    PolicyProfile,
    PolicyResolver,
    TaskSpecBuilder,
    TaskSpecParserPort,
)

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled at runtime if dependency is missing
    OpenAI = None

# OpenAIParserSettings keeps the parser runtime configuration explicit and testable.
@dataclass(frozen=True)
class OpenAIParserSettings:
    model: str = "gpt-5.4-nano"
    api_key: str | None = None
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "OpenAIParserSettings":
        # Merge process env and local .env files into one parser settings object.
        env_file_values = load_env_file_values()

        return cls(
            model=os.getenv(
                "LAYER1_OPENAI_MODEL",
                env_file_values.get("LAYER1_OPENAI_MODEL", "gpt-5.4-nano"),
            ),
            api_key=os.getenv("OPENAI_API_KEY", env_file_values.get("OPENAI_API_KEY")),
            timeout_seconds=float(
                os.getenv(
                    "LAYER1_OPENAI_TIMEOUT_SECONDS",
                    env_file_values.get("LAYER1_OPENAI_TIMEOUT_SECONDS", "30"),
                )
            ),
        )


class _PolicyProfilePayload(BaseModel):
    # Validate one policy profile before it is turned into runtime models.
    model_config = ConfigDict(extra="forbid")

    confidence_threshold: float = Field(..., ge=0.0, le=1.0)
    read_only_mode: bool = False


class _PolicyConfigPayload(BaseModel):
    # Validate the whole policy file so repository errors fail early and clearly.
    model_config = ConfigDict(extra="forbid")

    default_policy: _PolicyProfilePayload
    named_profiles: dict[str, _PolicyProfilePayload] = Field(default_factory=dict)
    sensitivity_overrides: dict[str, _PolicyProfilePayload | dict[str, Any]] = Field(
        default_factory=dict
    )


def build_task_spec_system_prompt() -> str:
    # Build the constrained system prompt used to parse raw requests into TaskSpec.
    allowed_sensitivity = ", ".join(level.value for level in SensitivityLevel)

    return f"""
You are the parser for layer 1 of an agentic system.
Your only task is to transform a user request into a structured task specification.

Return a structured object with these fields:
- goal
- context
- constraints
- sensitivity
- success_criteria
- execution_intent

Rules:
- Be concise and literal.
- Infer the main goal from the request.
- Keep context short and factual.
- Extract only explicit or strongly implied constraints.
- Set execution_intent to "proposal_only" when the user asks to draft, prepare, preview,
  or generate an operation without carrying it out.
- Set execution_intent to "execute_after_governance" only when the user explicitly asks
  to send, execute, publish, create, update, delete, or otherwise carry out an external
  operation. This field records requested intent and does not grant authority.
- Use one sensitivity from this exact list: {allowed_sensitivity}.
- If the task looks ordinary and non-sensitive, prefer "medium" or "low".
- Use "high" or "critical" when the task concerns regulated, personal, contractual, financial, security, or externally impactful actions.
- success_criteria should be practical completion conditions, not generic statements.
- Do not invent missing business details as if they were certain.
- Do not add fields outside the schema.
- Do not explain your reasoning.
""".strip()


def build_task_spec_user_prompt(
    *,
    raw_task_text: str,
    context_hint: str | None = None,
) -> str:
    # Keep the user prompt compact and explicit so parsing behavior remains predictable.
    context_block = context_hint if context_hint else "None"

    return f"""
Task text:
{raw_task_text}

Additional context hint:
{context_block}
""".strip()


class TaskSpecExtractionPayload(BaseModel):
    # Mirror the exact structure expected back from the LLM parser call.
    model_config = ConfigDict(extra="forbid")

    goal: str
    context: str = ""
    constraints: list[str] = Field(default_factory=list)
    sensitivity: SensitivityLevel = SensitivityLevel.MEDIUM
    success_criteria: list[str] = Field(default_factory=list)
    execution_intent: ExecutionIntent = ExecutionIntent.PROPOSAL_ONLY


class OpenAITaskSpecParser(TaskSpecParserPort):
    parser_name = "openai_task_spec_parser"
    prompt_version = "task_spec_v1"

    def __init__(self, settings: OpenAIParserSettings | None = None) -> None:
        # Validate parser prerequisites once at construction time.
        self._settings = settings or OpenAIParserSettings.from_env()

        if OpenAI is None:
            raise RuntimeError(
                "The 'openai' package is not installed. Install it before using the OpenAI parser."
            )
        if not self._settings.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is missing. Set it in the process environment or in .env."
            )

        self._client = OpenAI(
            api_key=self._settings.api_key,
            timeout=self._settings.timeout_seconds,
        )

    def parse(self, *, raw_task_text: str, context_hint: str | None = None) -> TaskSpec:
        # Ask OpenAI for a typed extraction payload and convert it into the domain model.
        try:
            response = self._request_parse_response(
                raw_task_text=raw_task_text,
                context_hint=context_hint,
            )
        except Exception as exc:  # pragma: no cover - network/client errors
            raise InvalidTaskSpecError(f"OpenAI parser request failed: {exc}") from exc

        payload = getattr(response, "output_parsed", None)
        if payload is None:
            raise InvalidTaskSpecError("OpenAI parser returned no structured output.")

        try:
            return TaskSpec(**payload.model_dump())
        except Exception as exc:
            raise InvalidTaskSpecError(
                f"Structured output could not be converted to TaskSpec: {exc}"
            ) from exc

    # Retry only transient provider failures so demos do not crash on one 429/503.
    @llm_retry
    def _request_parse_response(self, *, raw_task_text: str, context_hint: str | None) -> object:
        return self._client.responses.parse(
            model=self._settings.model,
            input=[
                {
                    "role": "system",
                    "content": build_task_spec_system_prompt(),
                },
                {
                    "role": "user",
                    "content": build_task_spec_user_prompt(
                        raw_task_text=raw_task_text,
                        context_hint=context_hint,
                    ),
                },
            ],
            text_format=TaskSpecExtractionPayload,
        )


class JsonExecutionPolicyRepository(ExecutionPolicyRepositoryPort):
    # This repository resolves policy data from the JSON configuration file.
    def __init__(self, config_path: str | Path) -> None:
        self._config_path = Path(config_path)
        self._config = self._load_config(self._config_path)

    def get_policy_profile(
        self,
        *,
        profile_name: str | None,
        sensitivity: SensitivityLevel,
    ) -> PolicyProfile:
        # Merge default policy with progressively more specific overrides.
        if profile_name and profile_name != "default":
            named_profiles = self._config.get("named_profiles", {})
            profile_data = named_profiles.get(profile_name)
            if profile_data is None:
                raise ValueError(f"Unknown policy profile: {profile_name}")
            return self._to_policy_profile(name=profile_name, data=profile_data)

        merged_policy = deepcopy(self._config["default_policy"])

        sensitivity_override = self._config.get("sensitivity_overrides", {}).get(
            sensitivity.value
        )
        if sensitivity_override:
            merged_policy = self._deep_merge(merged_policy, sensitivity_override)

        return self._to_policy_profile(name="default", data=merged_policy)

    @staticmethod
    def _load_config(config_path: Path) -> dict[str, Any]:
        # Fail early if the configuration file is missing or structurally invalid.
        if not config_path.exists():
            raise FileNotFoundError(f"Policy config not found: {config_path}")
        with config_path.open("r", encoding="utf-8") as file:
            raw_config = json.load(file)

        try:
            _PolicyConfigPayload.model_validate(raw_config)
        except ValidationError as exc:
            raise ValueError(f"Invalid policy config file '{config_path}': {exc}") from exc

        return raw_config

    @staticmethod
    def _to_policy_profile(*, name: str, data: dict[str, Any]) -> PolicyProfile:
        # Translate raw JSON data into the typed PolicyProfile used by the service layer.
        return PolicyProfile(
            name=name,
            confidence_threshold=data["confidence_threshold"],
            read_only_mode=data.get("read_only_mode", False),
        )

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        # Merge nested configuration sections without mutating the base dictionary.
        merged = deepcopy(base)
        for key, value in override.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = JsonExecutionPolicyRepository._deep_merge(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged


class SystemClock:
    # Production clock implementation for Layer 1.
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class Sha256FingerprintService(InputFingerprintPort):
    # Production fingerprint implementation for stable raw input tracing.
    def fingerprint(self, raw_text: str) -> str:
        return sha256(raw_text.encode("utf-8")).hexdigest()


def build_intake_service(
    *,
    policy_config_path: str | Path | None = None,
    openai_settings: OpenAIParserSettings | None = None,
) -> IntakeService:
    # Build the concrete production dependencies used by the Layer 1 entrypoints.
    # Resolve the config path once here so every entrypoint shares the same default behavior.
    resolved_policy_path = (
        Path(policy_config_path) if policy_config_path else get_default_policy_config_path()
    )

    parser = OpenAITaskSpecParser(settings=openai_settings)
    repository = JsonExecutionPolicyRepository(config_path=resolved_policy_path)

    return IntakeService(
        task_spec_builder=TaskSpecBuilder(parser=parser),
        policy_resolver=PolicyResolver(repository=repository),
        validator=IntakeResultValidator(),
        clock=SystemClock(),
        fingerprint_service=Sha256FingerprintService(),
    )


def get_default_policy_config_path() -> Path:
    # Keep the default config path in one place so entrypoints stay aligned.
    return Path(__file__).resolve().parents[2] / "configs" / "layer1_execution_policies.json"


__all__ = [
    "build_intake_service",
    "get_default_policy_config_path",
    "JsonExecutionPolicyRepository",
    "OpenAIParserSettings",
    "OpenAITaskSpecParser",
    "Sha256FingerprintService",
    "SystemClock",
]
