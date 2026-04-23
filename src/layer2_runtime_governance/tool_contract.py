from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


# ToolName keeps the tool surface explicit and audit-friendly.
class ToolName(str, Enum):
    WEB_SEARCH = "web_search"
    FETCH_URL_TEXT = "fetch_url_text"


@dataclass(frozen=True)
class ToolRequest:
    # Tool requests are typed so the actor cannot emit opaque free-form tool calls.
    plan_step_id: str
    tool_name: ToolName
    reason: str
    query: str | None = None
    url: str | None = None
    max_results: int = 5
    extractive_goal: str | None = None


@dataclass(frozen=True)
class ToolSource:
    # Tool sources preserve provenance for later observation and audit.
    title: str
    locator: str
    snippet: str | None = None


@dataclass(frozen=True)
class ToolExecutionResult:
    # Tool execution always returns a structured result, including failures.
    request: ToolRequest
    success: bool
    summary: str
    content: str | None = None
    sources: tuple[ToolSource, ...] = ()
    error_message: str | None = None


@dataclass(frozen=True)
class ObservedEvidence:
    # Observed evidence is what the actor can later use as grounded context.
    evidence_id: str
    plan_step_id: str
    title: str
    locator: str
    excerpt: str
    source_tool: ToolName


class ToolGatewayPort(Protocol):
    # The gateway port decouples runtime orchestration from the concrete tool backend.
    gateway_name: str

    def execute(self, *, tool_request: ToolRequest) -> ToolExecutionResult: ...


__all__ = [
    "ObservedEvidence",
    "ToolExecutionResult",
    "ToolGatewayPort",
    "ToolName",
    "ToolRequest",
    "ToolSource",
]
