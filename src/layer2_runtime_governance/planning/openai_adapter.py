from __future__ import annotations

from typing import Protocol, cast

from pydantic import BaseModel

from llm_retry import llm_retry

try:
    from openai import OpenAI as _OpenAI
except ImportError:  # pragma: no cover - handled when a concrete adapter is built
    _OpenAI = None


class _ParsedResponsesPort(Protocol):
    # Describe only the structured response surface shared by planning adapters.
    def parse(
        self,
        *,
        model: str,
        input: list[dict[str, str]],  # noqa
        text_format: type[BaseModel],
    ) -> object: ...


class ParsedOpenAIClient(Protocol):
    responses: _ParsedResponsesPort


# Validate shared OpenAI prerequisites and construct one bounded client.
def build_parsed_openai_client(
    *,
    api_key: str | None,
    timeout_seconds: float,
    adapter_label: str,
) -> ParsedOpenAIClient:
    if _OpenAI is None:
        raise RuntimeError(
            "The 'openai' package is not installed. "
            f"Install it before using the Layer 2 {adapter_label}."
        )
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is missing. Set it in the process environment or in .env."
        )

    return cast(
        ParsedOpenAIClient,
        _OpenAI(api_key=api_key, timeout=timeout_seconds),
    )


# Retry the provider call while keeping each adapter's prompt and schema explicit.
@llm_retry
def request_parsed_openai_response(
    *,
    client: ParsedOpenAIClient,
    model: str,
    system_prompt: str,
    user_prompt: str,
    text_format: type[BaseModel],
) -> object:
    return client.responses.parse(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        text_format=text_format,
    )


# Enforce the parsed-response invariant without coupling it to one decision type.
def require_parsed_output(
    *,
    response: object,
    error_type: type[Exception],
    missing_output_message: str,
) -> object:
    payload = getattr(response, "output_parsed", None)
    if payload is None:
        raise error_type(missing_output_message)
    return payload


__all__ = [
    "ParsedOpenAIClient",
    "build_parsed_openai_client",
    "request_parsed_openai_response",
    "require_parsed_output",
]
