from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol
from uuid import uuid4


# ToolName keeps the tool surface explicit and audit-friendly.
class ToolName(str, Enum):
    WEB_SEARCH = "web_search"
    FETCH_URL_TEXT = "fetch_url_text"
    CONTROLLED_DOCUMENT_READ = "controlled_document.read"
    CONTROLLED_DATASET_READ = "controlled_dataset.read"
    MCP_LOCAL_TEST_ECHO = "mcp.local_test.echo"
    MCP_LOCAL_TEST_READ_FIXTURE = "mcp.local_test.read_fixture"
    MCP_LOCAL_TEST_TEXT_STATS = "mcp.local_test.text_stats"
    MCP_LOCAL_TEST_CALCULATE_EXPRESSION = "mcp.local_test.calculate_expression"


class ToolErrorCode(str, Enum):
    FORBIDDEN = "forbidden"
    BYTE_LIMIT = "byte_limit"
    BOT_CHALLENGE = "bot_challenge"
    EMPTY_EXTRACTION = "empty_extraction"
    TIMEOUT = "timeout"
    CIRCUIT_OPEN = "circuit_open"
    DUPLICATE_REQUEST = "duplicate_request"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ToolRequest:
    # Tool requests are typed so the actor cannot emit opaque free-form tool calls.
    plan_step_id: str
    tool_name: ToolName
    reason: str
    request_id: str = field(default_factory=lambda: str(uuid4()))
    query: str | None = None
    url: str | None = None
    text: str | None = None
    path: str | None = None
    expression: str | None = None
    timezone: str | None = None
    limit: int | None = None
    max_results: int = 5
    extractive_goal: str | None = None
    document_id: str | None = None
    dataset_id: str | None = None


@dataclass(frozen=True)
class ToolObservation:
    # Adapters expose content and any source-attributed timestamp observed at the capability boundary.
    observation_id: str
    content: str
    locator: str | None = None
    source_timestamp: datetime | None = None


@dataclass(frozen=True)
class ToolExecutionResult:
    # Tool execution always returns a structured result, including failures.
    request: ToolRequest
    success: bool
    summary: str
    content: str | None = None
    observations: tuple[ToolObservation, ...] = ()
    error_message: str | None = None
    error_code: ToolErrorCode | None = None
    truncated: bool = False
    extractability_limited: bool = False

class ToolGatewayPort(Protocol):
    # The gateway port decouples runtime orchestration from the concrete tool backend.
    gateway_name: str

    def execute(self, *, tool_request: ToolRequest) -> ToolExecutionResult: ...


__all__ = [
    "ToolErrorCode",
    "ToolExecutionResult",
    "ToolGatewayPort",
    "ToolName",
    "ToolObservation",
    "ToolRequest",
]
