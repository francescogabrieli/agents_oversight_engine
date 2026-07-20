from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


# ToolName keeps the tool surface explicit and audit-friendly.
class ToolName(str, Enum):
    WEB_SEARCH = "web_search"
    FETCH_URL_TEXT = "fetch_url_text"
    MCP_LOCAL_TEST_ECHO = "mcp.local_test.echo"
    MCP_LOCAL_TEST_READ_FIXTURE = "mcp.local_test.read_fixture"
    MCP_LOCAL_TEST_TEXT_STATS = "mcp.local_test.text_stats"
    MCP_LOCAL_TEST_CALCULATE_EXPRESSION = "mcp.local_test.calculate_expression"


class EvidenceSupportLevel(str, Enum):
    # EvidenceSupportLevel states how much claim precision one observed evidence item can safely support.
    LOCAL_ONLY = "local_only"
    SOURCE_DISCOVERY = "source_discovery"
    HIGH_LEVEL_SUMMARY = "high_level_summary"
    DETAILED_EXTRACTED = "detailed_extracted"
    PRIMARY_LEGAL = "primary_legal"


class EvidenceSourceKind(str, Enum):
    # EvidenceSourceKind distinguishes between operator-provided task facts and tool observations.
    INTAKE_PROVIDED = "intake_provided"
    TOOL_OBSERVATION = "tool_observation"


@dataclass(frozen=True)
class ToolRequest:
    # Tool requests are typed so the actor cannot emit opaque free-form tool calls.
    plan_step_id: str
    tool_name: ToolName
    reason: str
    query: str | None = None
    url: str | None = None
    text: str | None = None
    path: str | None = None
    expression: str | None = None
    timezone: str | None = None
    limit: int | None = None
    max_results: int = 5
    extractive_goal: str | None = None


@dataclass(frozen=True)
class ToolSource:
    # Tool sources preserve provenance for later observation and audit.
    title: str
    locator: str
    snippet: str | None = None
    # Specialized adapters may declare a conservative support ceiling for this source.
    support_level_hint: EvidenceSupportLevel | None = None


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
    source_kind: EvidenceSourceKind = EvidenceSourceKind.TOOL_OBSERVATION
    source_tool: ToolName | None = None
    support_level: EvidenceSupportLevel = EvidenceSupportLevel.HIGH_LEVEL_SUMMARY


class ToolGatewayPort(Protocol):
    # The gateway port decouples runtime orchestration from the concrete tool backend.
    gateway_name: str

    def execute(self, *, tool_request: ToolRequest) -> ToolExecutionResult: ...


__all__ = [
    "EvidenceSourceKind",
    "EvidenceSupportLevel",
    "ObservedEvidence",
    "ToolExecutionResult",
    "ToolGatewayPort",
    "ToolName",
    "ToolRequest",
    "ToolSource",
]
