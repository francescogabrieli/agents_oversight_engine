"""Concrete actor providers hidden behind the ActorPort contract."""

from __future__ import annotations

from importlib import import_module
from typing import Any


__all__ = ["BedrockNovaActor", "OpenAIActor"]


# Defer provider imports so settings can load transport configuration without a cycle.
def __getattr__(name: str) -> Any:
    if name == "BedrockNovaActor":
        value = getattr(import_module(".bedrock", __name__), name)
    elif name == "OpenAIActor":
        value = getattr(import_module(".openai", __name__), name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
