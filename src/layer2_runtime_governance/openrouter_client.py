from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class OpenRouterRuntimeSettings:
    # OpenRouterRuntimeSettings keeps the temporary actor test model explicit.
    model: str = "openai/gpt-oss-120b:free"
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
                            "openai/gpt-oss-120b:free",
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

        content = _extract_message_content(response_payload)
        return _parse_json_object(content)


# Add strict JSON-output instructions around each role-specific prompt.
def _json_system_prompt(system_prompt: str, expected_shape: str) -> str:
    return (
        f"{system_prompt}\n\n"
        "You must return exactly one valid JSON object. "
        "Do not wrap it in Markdown. Do not include analysis text, explanations, or comments. "
        f"The JSON object must follow this shape:\n{expected_shape}"
    )


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
