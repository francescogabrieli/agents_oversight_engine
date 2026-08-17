# Live transport validation uses ValueError so malformed payloads remain client errors.
# ruff: noqa: TRY004
from __future__ import annotations

from typing import Any


MAX_COMMUNICATION_COPY_RECIPIENTS = 20


# Normalize optional live text while rejecting implicit non-string conversion.
def normalize_optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    normalized = value.strip()
    return normalized or None


# Normalize a required live text field without collapsing meaningful internal whitespace.
def require_text(value: Any, *, field_name: str) -> str:
    normalized = normalize_optional_text(value, field_name=field_name)
    if normalized is None:
        raise ValueError(f"{field_name} is required.")
    return normalized


# Validate a complete communication body while preserving its semantic whitespace.
def normalize_communication_body(
    value: Any,
    *,
    field_name: str = "External communication body",
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        raise ValueError(f"{field_name} is required.")
    return normalized


# Normalize and bound copied recipients before a live edit enters runtime state.
def normalize_copy_recipients(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"Communication {field_name} must be an array of strings.")
    recipients = tuple(
        normalized
        for item in value
        if (
            normalized := normalize_optional_text(
                item,
                field_name=f"Communication {field_name} recipient",
            )
        )
        is not None
    )
    if len(recipients) > MAX_COMMUNICATION_COPY_RECIPIENTS:
        raise ValueError(
            f"Communication {field_name} exceeds the recipient bound of "
            f"{MAX_COMMUNICATION_COPY_RECIPIENTS}."
        )
    return recipients


__all__ = [
    "MAX_COMMUNICATION_COPY_RECIPIENTS",
    "normalize_communication_body",
    "normalize_copy_recipients",
    "normalize_optional_text",
    "require_text",
]
