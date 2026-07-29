from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llm_retry import llm_retry

from ...infrastructure.environment import candidate_env_paths, load_env_file_values

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:  # pragma: no cover - handled at runtime if dependency is missing
    boto3 = None
    Config = None
    BotoCoreError = Exception
    ClientError = Exception


_DEFAULT_BEDROCK_MODEL_ID = "eu.amazon.nova-2-lite-v1:0"


class BedrockRuntimeError(RuntimeError):
    # Preserve a normalized HTTP status so shared retry handling can stay provider-agnostic.
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class BedrockRuntimeSettings:
    # Keep a safe default while allowing the deployment to select another Bedrock Converse model.
    model_id: str = _DEFAULT_BEDROCK_MODEL_ID
    region_name: str = "eu-central-1"
    profile_name: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    session_token: str | None = None
    read_timeout_seconds: float = 3600.0
    connect_timeout_seconds: float = 10.0

    @classmethod
    # Build typed Bedrock settings from the supported local configuration sources.
    def from_env(cls) -> "BedrockRuntimeSettings":
        # Read values from process variables first, then the local ignored env files.
        env_file_values = _load_env_file_values()
        return cls(
            model_id=os.getenv(
                "LAYER2_ACTOR_BEDROCK_MODEL_ID",
                env_file_values.get(
                    "LAYER2_ACTOR_BEDROCK_MODEL_ID", _DEFAULT_BEDROCK_MODEL_ID
                ),
            ),
            region_name=os.getenv(
                "AWS_REGION",
                env_file_values.get("AWS_REGION", "eu-central-1"),
            ),
            profile_name=os.getenv("AWS_PROFILE", env_file_values.get("AWS_PROFILE")),
            access_key_id=os.getenv(
                "AWS_ACCESS_KEY_ID", env_file_values.get("AWS_ACCESS_KEY_ID")
            ),
            secret_access_key=os.getenv(
                "AWS_SECRET_ACCESS_KEY", env_file_values.get("AWS_SECRET_ACCESS_KEY")
            ),
            session_token=os.getenv(
                "AWS_SESSION_TOKEN", env_file_values.get("AWS_SESSION_TOKEN"),
            ),
            read_timeout_seconds=float(
                os.getenv(
                    "LAYER2_ACTOR_BEDROCK_READ_TIMEOUT_SECONDS",
                    env_file_values.get("LAYER2_ACTOR_BEDROCK_READ_TIMEOUT_SECONDS", "3600"),
                )
            ),
            connect_timeout_seconds=float(
                os.getenv(
                    "LAYER2_ACTOR_BEDROCK_CONNECT_TIMEOUT_SECONDS",
                    env_file_values.get("LAYER2_ACTOR_BEDROCK_CONNECT_TIMEOUT_SECONDS", "10"),
                )
            ),
        )


class BedrockChatClient:
    # Wrap Amazon Bedrock Converse while preserving the runtime's one-action actor contract.
    # Validate credentials and build the SDK client once for the actor lifetime.
    def __init__(self, settings: BedrockRuntimeSettings | None = None) -> None:
        self._settings = settings or BedrockRuntimeSettings.from_env()
        _validate_settings(settings=self._settings)

        if boto3 is None or Config is None:
            raise BedrockRuntimeError(
                "The 'boto3' package is not installed. Install it before using the Bedrock actor."
            )

        # An explicit profile must win over stale static values in local env files.
        session = (
            boto3.Session(profile_name=self._settings.profile_name)
            if self._settings.profile_name
            else boto3.Session(
                aws_access_key_id=self._settings.access_key_id,
                aws_secret_access_key=self._settings.secret_access_key,
                aws_session_token=self._settings.session_token,
            )
        )
        self._client = session.client(
            "bedrock-runtime",
            region_name=self._settings.region_name,
            config=Config(
                connect_timeout=self._settings.connect_timeout_seconds,
                read_timeout=self._settings.read_timeout_seconds,
                retries={"mode": "standard", "max_attempts": 3},
            ),
        )

    # Ask Nova for exactly one typed runtime action using Bedrock's Converse tool contract.
    @llm_retry
    def complete_tool_calls(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict[str, object]],
    ) -> dict[str, Any]:
        try:
            response_payload = self._client.converse(
                modelId=self._settings.model_id,
                system=[{"text": system_prompt}],
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": user_prompt}],
                    }
                ],
                toolConfig={
                    "tools": _to_bedrock_tools(tools),
                    "toolChoice": {"any": {}},
                },
            )
        except ClientError as exc:
            raise _normalize_client_error(exc) from exc
        except (BotoCoreError, TimeoutError) as exc:
            raise BedrockRuntimeError(f"Bedrock runtime request failed: {exc}") from exc

        return _extract_tool_call_payloads(response_payload)

    # Preserve the one-call facade for older direct adapter callers.
    def complete_tool_call(self, **kwargs) -> dict[str, Any]:
        payloads = self.complete_tool_calls(**kwargs)
        if len(payloads) != 1:
            raise BedrockRuntimeError("Bedrock returned multiple tool uses; use complete_tool_calls.")
        return payloads[0]


# Validate only generic Bedrock transport prerequisites before making a network request.
def _validate_settings(*, settings: BedrockRuntimeSettings) -> None:
    if not settings.model_id or not settings.model_id.strip():
        raise BedrockRuntimeError(
            "LAYER2_ACTOR_BEDROCK_MODEL_ID must be a non-empty Bedrock model or inference-profile identifier."
        )
    if not settings.region_name or not settings.region_name.strip():
        raise BedrockRuntimeError(
            "AWS_REGION must be a non-empty AWS region for the selected Bedrock model."
        )
    if (
        not settings.profile_name
        and (not settings.access_key_id or not settings.secret_access_key)
    ):
        raise BedrockRuntimeError(
            "Configure AWS_PROFILE or set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY "
            "for the Bedrock actor."
        )


# Convert the existing runtime action definitions into Bedrock Converse tool specifications.
def _to_bedrock_tools(tools: list[dict[str, object]]) -> list[dict[str, object]]:
    bedrock_tools: list[dict[str, object]] = []
    for tool in tools:
        if tool.get("type") != "function":
            raise BedrockRuntimeError("Bedrock tools must be function tools.")

        name = tool.get("name")
        description = tool.get("description")
        parameters = tool.get("parameters")
        if not isinstance(name, str) or not isinstance(description, str):
            raise BedrockRuntimeError("Bedrock tool specs require name and description.")
        if not isinstance(parameters, dict):
            raise BedrockRuntimeError("Bedrock tool specs require JSON schema parameters.")

        # Nova accepts only type, properties, and required at the top schema level.
        schema = {
            key: value
            for key, value in parameters.items()
            if key in {"type", "properties", "required"}
        }
        if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
            raise BedrockRuntimeError("Bedrock tool schemas must declare an object with properties.")

        bedrock_tools.append(
            {
                "toolSpec": {
                    "name": name,
                    "description": description,
                    "inputSchema": {"json": schema},
                }
            }
        )
    return bedrock_tools


# Extract one Bedrock toolUse block and normalize it to the existing actor payload shape.
def _extract_tool_call_payloads(response_payload: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(response_payload, dict):
        raise BedrockRuntimeError("Bedrock returned a malformed Converse response.")

    output = response_payload.get("output")
    if not isinstance(output, dict):
        raise BedrockRuntimeError("Bedrock Converse response is missing output.")
    message = output.get("message")
    if not isinstance(message, dict):
        raise BedrockRuntimeError("Bedrock Converse response is missing output message.")
    content = message.get("content")
    if not isinstance(content, list):
        raise BedrockRuntimeError("Bedrock Converse response is missing output content.")

    tool_uses = [
        block["toolUse"]
        for block in content
        if isinstance(block, dict) and isinstance(block.get("toolUse"), dict)
    ]
    if not tool_uses:
        raise BedrockRuntimeError("Bedrock returned no tool use.")
    payloads: list[dict[str, Any]] = []
    for tool_use in tool_uses:
        name = tool_use.get("name")
        arguments = tool_use.get("input")
        if not isinstance(name, str) or not name.strip():
            raise BedrockRuntimeError("Bedrock tool use is missing tool name.")
        if not isinstance(arguments, dict):
            raise BedrockRuntimeError("Bedrock tool use input must be an object.")
        payloads.append({"tool_name": name, "arguments": arguments})
    return tuple(payloads)


# Preserve the former parser path for direct one-call compatibility.
def _extract_tool_call_payload(response_payload: object) -> dict[str, Any]:
    payloads = _extract_tool_call_payloads(response_payload)
    if len(payloads) != 1:
        raise BedrockRuntimeError("Bedrock returned multiple tool uses; use _extract_tool_call_payloads.")
    return payloads[0]


# Preserve Bedrock status information so llm_retry can retry throttling and temporary outages.
def _normalize_client_error(exc: Exception) -> BedrockRuntimeError:
    response = getattr(exc, "response", {})
    metadata = response.get("ResponseMetadata", {}) if isinstance(response, dict) else {}
    error = response.get("Error", {}) if isinstance(response, dict) else {}
    status_code = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
    error_code = error.get("Code") if isinstance(error, dict) else None
    detail = error.get("Message") if isinstance(error, dict) else None
    return BedrockRuntimeError(
        f"Bedrock runtime error {error_code or 'unknown'}: {detail or exc}",
        status_code=status_code if isinstance(status_code, int) else None,
    )


# Load simple KEY=VALUE pairs from the same ignored locations used by the live runtime.
def _load_env_file_values() -> dict[str, str]:
    return dict(load_env_file_values())


# Search both repository-root and src-level env files for local execution.
def _candidate_env_paths() -> list[Path]:
    return list(candidate_env_paths())


__all__ = [
    "BedrockChatClient",
    "BedrockRuntimeError",
    "BedrockRuntimeSettings",
]
