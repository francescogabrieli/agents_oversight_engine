from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


_REQUIRED_OPENROUTER_MODEL = "gpt-oss-120b"


@dataclass(frozen=True)
class OpenRouterRuntimeSettings:
    # OpenRouterRuntimeSettings keeps the temporary actor test model explicit.
    model: str = "gpt-oss-120b"
    api_key: str | None = None
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = 120.0
    temperature: float = 0.0

    @classmethod
    def from_env(cls, *, model_env_key: str | None = None) -> "OpenRouterRuntimeSettings":
        # Merge process env and local .env files into one OpenRouter settings object.
        env_file_values = _load_env_file_values()
        resolved_model_key = model_env_key or "LAYER2_OPENROUTER_MODEL"
        return cls(
            model=os.getenv(
                resolved_model_key,
                env_file_values.get(
                    resolved_model_key,
                    os.getenv(
                        "LAYER2_OPENROUTER_MODEL",
                        env_file_values.get(
                            "LAYER2_OPENROUTER_MODEL",
                            "gpt-oss-120b",
                        ),
                    ),
                ),
            ),
            api_key=os.getenv("OPENROUTER_API_KEY", env_file_values.get("OPENROUTER_API_KEY")),
            base_url=os.getenv(
                "LAYER2_OPENROUTER_BASE_URL",
                env_file_values.get("LAYER2_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            ).rstrip("/"),
            timeout_seconds=float(
                os.getenv(
                    "LAYER2_OPENROUTER_TIMEOUT_SECONDS",
                    env_file_values.get("LAYER2_OPENROUTER_TIMEOUT_SECONDS", "120"),
                )
            ),
            temperature=float(
                os.getenv(
                    "LAYER2_OPENROUTER_TEMPERATURE",
                    env_file_values.get("LAYER2_OPENROUTER_TEMPERATURE", "0"),
                )
            ),
        )


class OpenRouterRuntimeError(RuntimeError):
    """Raised when OpenRouter cannot produce a valid actor response."""


class OpenRouterChatClient:
    # OpenRouterChatClient wraps the OpenAI-compatible chat completions endpoint.
    def __init__(self, settings: OpenRouterRuntimeSettings | None = None) -> None:
        self._settings = settings or OpenRouterRuntimeSettings.from_env()
        if not self._settings.api_key:
            raise OpenRouterRuntimeError(
                "OPENROUTER_API_KEY is missing. Set it in the process environment or .env."
            )
        _validate_required_model(model=self._settings.model)

    # Ask OpenRouter for a JSON object and return it as a plain dictionary.
    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        expected_shape: str,
    ) -> dict[str, Any]:
        payload = {
            "model": self._settings.model,
            "temperature": self._settings.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _json_system_prompt(system_prompt, expected_shape)},
                {"role": "user", "content": user_prompt},
            ],
        }

        try:
            request = Request(
                f"{self._settings.base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self._settings.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://localhost",
                    "X-Title": "tesi-layer2-runtime",
                },
                method="POST",
            )
            with urlopen(request, timeout=self._settings.timeout_seconds) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OpenRouterRuntimeError(
                f"OpenRouter HTTP error {exc.code}: {exc.reason}. {detail}"
            ) from exc
        except URLError as exc:
            raise OpenRouterRuntimeError(f"OpenRouter is not reachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise OpenRouterRuntimeError(
                f"OpenRouter request timed out after {self._settings.timeout_seconds} seconds."
            ) from exc
        except json.JSONDecodeError as exc:
            raise OpenRouterRuntimeError(
                f"OpenRouter returned invalid response JSON: {exc}"
            ) from exc

        _validate_response_model(
            response_payload=response_payload,
            requested_model=self._settings.model,
        )
        content = _extract_message_content(response_payload)
        return _parse_json_object(content)

    # Ask OpenRouter for a standard OpenAI-compatible tool call.
    def complete_tool_call(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict[str, object]],
    ) -> dict[str, Any]:
        payload = {
            "model": self._settings.model,
            "temperature": self._settings.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "tools": _to_chat_completion_tools(tools),
            "tool_choice": "required",
            "parallel_tool_calls": False,
        }

        try:
            request = Request(
                f"{self._settings.base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self._settings.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://localhost",
                    "X-Title": "tesi-layer2-runtime",
                },
                method="POST",
            )
            with urlopen(request, timeout=self._settings.timeout_seconds) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OpenRouterRuntimeError(
                f"OpenRouter HTTP error {exc.code}: {exc.reason}. {detail}"
            ) from exc
        except URLError as exc:
            raise OpenRouterRuntimeError(f"OpenRouter is not reachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise OpenRouterRuntimeError(
                f"OpenRouter request timed out after {self._settings.timeout_seconds} seconds."
            ) from exc
        except json.JSONDecodeError as exc:
            raise OpenRouterRuntimeError(
                f"OpenRouter returned invalid response JSON: {exc}"
            ) from exc

        _validate_response_model(
            response_payload=response_payload,
            requested_model=self._settings.model,
        )
        return _extract_tool_call_payload(response_payload)


# Add strict JSON-output instructions around each role-specific prompt.
def _json_system_prompt(system_prompt: str, expected_shape: str) -> str:
    return (
        f"{system_prompt}\n\n"
        "You must return exactly one valid JSON object. "
        "Do not wrap it in Markdown. Do not include analysis text, explanations, or comments. "
        f"The JSON object must follow this shape:\n{expected_shape}"
    )


# Enforce the single actor model selected for the temporary OpenRouter test backend.
def _validate_required_model(*, model: str) -> None:
    normalized_model = _normalize_model_name(model)
    if normalized_model != _REQUIRED_OPENROUTER_MODEL:
        raise OpenRouterRuntimeError(
            f"OpenRouter actor model must be '{_REQUIRED_OPENROUTER_MODEL}', got '{model}'."
        )


# Refuse provider-side routing/fallback to any model except the required actor model.
def _validate_response_model(*, response_payload: dict[str, Any], requested_model: str) -> None:
    response_model = response_payload.get("model")
    if response_model is None:
        return
    if not isinstance(response_model, str):
        raise OpenRouterRuntimeError("OpenRouter response field 'model' must be a string.")

    normalized_response_model = _normalize_model_name(response_model)
    if normalized_response_model != _REQUIRED_OPENROUTER_MODEL:
        raise OpenRouterRuntimeError(
            f"OpenRouter served unexpected model '{response_model}' for required model "
            f"'{_REQUIRED_OPENROUTER_MODEL}'."
        )


# Normalize model names so provider prefixes and case cannot bypass the guard.
def _normalize_model_name(model: str) -> str:
    normalized = model.strip().lower()
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    return normalized


# Extract the first assistant message from an OpenAI-compatible chat response.
def _extract_message_content(response_payload: dict[str, Any]) -> str:
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OpenRouterRuntimeError("OpenRouter returned no choices.")

    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise OpenRouterRuntimeError("OpenRouter returned a malformed message.")

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise OpenRouterRuntimeError("OpenRouter returned an empty assistant message.")

    return content


# Convert Responses-style function specs into Chat Completions tool specs.
def _to_chat_completion_tools(tools: list[dict[str, object]]) -> list[dict[str, object]]:
    chat_tools: list[dict[str, object]] = []
    for tool in tools:
        if tool.get("type") != "function":
            raise OpenRouterRuntimeError("OpenRouter tools must be function tools.")

        name = tool.get("name")
        description = tool.get("description")
        parameters = tool.get("parameters")
        if not isinstance(name, str) or not isinstance(description, str):
            raise OpenRouterRuntimeError("OpenRouter tool specs require name and description.")
        if not isinstance(parameters, dict):
            raise OpenRouterRuntimeError("OpenRouter tool specs require JSON schema parameters.")

        chat_tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            }
        )
    return chat_tools


# Extract the first standard tool call from a non-streaming chat response.
def _extract_tool_call_payload(response_payload: dict[str, Any]) -> dict[str, Any]:
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OpenRouterRuntimeError("OpenRouter returned no choices.")

    choice = choices[0]
    if not isinstance(choice, dict):
        raise OpenRouterRuntimeError("OpenRouter returned a malformed choice.")

    message = choice.get("message")
    if not isinstance(message, dict):
        raise OpenRouterRuntimeError("OpenRouter returned a malformed message.")

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        if len(tool_calls) > 1:
            raise OpenRouterRuntimeError("OpenRouter returned more than one tool call.")
        return _parse_chat_tool_call(tool_calls[0])

    # Some providers ignore tool_choice and return a JSON payload in content.
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        try:
            return _parse_json_object(content)
        except OpenRouterRuntimeError:
            return _plain_text_content_to_actor_payload(content)

    finish_reason = choice.get("finish_reason")
    raise OpenRouterRuntimeError(
        f"OpenRouter returned no tool call and no assistant content. finish_reason={finish_reason!r}."
    )


# Parse one OpenAI-compatible chat tool call into the actor payload shape.
def _parse_chat_tool_call(tool_call: object) -> dict[str, Any]:
    if not isinstance(tool_call, dict):
        raise OpenRouterRuntimeError("OpenRouter returned a malformed tool call.")

    function_payload = tool_call.get("function")
    if not isinstance(function_payload, dict):
        raise OpenRouterRuntimeError("OpenRouter tool call is missing function payload.")

    name = function_payload.get("name")
    raw_arguments = function_payload.get("arguments")
    if not isinstance(name, str) or not name.strip():
        raise OpenRouterRuntimeError("OpenRouter tool call is missing function name.")
    if not isinstance(raw_arguments, str):
        raise OpenRouterRuntimeError("OpenRouter tool call arguments must be a JSON string.")

    try:
        parsed_arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as exc:
        raise OpenRouterRuntimeError(
            f"OpenRouter tool call arguments were malformed JSON: {exc}"
        ) from exc

    if not isinstance(parsed_arguments, dict):
        raise OpenRouterRuntimeError("OpenRouter tool call arguments must decode to an object.")

    return {"tool_name": name, "arguments": parsed_arguments}


# Convert non-standard plain text into a conservative actor progress payload.
def _plain_text_content_to_actor_payload(content: str) -> dict[str, Any]:
    stripped = content.strip()
    return {
        "tool_name": "emit_intermediate_output",
        "arguments": {
            "summary": "OpenRouter returned plain text instead of a standard tool call.",
            "output_text": stripped,
            "progress_note": "Provider output was preserved as intermediate output for governance review.",
            "residual_uncertainty": (
                "The actor provider did not use the required tool-calling protocol."
            ),
            "confidence": 0.3,
        },
    }


# Parse a JSON object, tolerating minor provider preambles around it.
def _parse_json_object(raw_content: str) -> dict[str, Any]:
    stripped = raw_content.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise OpenRouterRuntimeError("OpenRouter response did not contain a JSON object.")
        try:
            parsed = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise OpenRouterRuntimeError(
                f"OpenRouter returned malformed JSON content: {exc}"
            ) from exc

    if not isinstance(parsed, dict):
        raise OpenRouterRuntimeError("OpenRouter JSON response must be an object.")
    return parsed


# Load simple KEY=VALUE pairs from supported env files.
def _load_env_file_values() -> dict[str, str]:
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


# Search both repository root and src-level env files for local development.
def _candidate_env_paths() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[2]
    return [repo_root / ".env", repo_root / "src" / ".env"]


__all__ = [
    "OpenRouterChatClient",
    "OpenRouterRuntimeError",
    "OpenRouterRuntimeSettings",
]
