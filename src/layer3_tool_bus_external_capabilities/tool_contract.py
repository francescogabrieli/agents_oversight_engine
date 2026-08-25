from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4


_TRACKING_QUERY_KEYS = frozenset(
    {
        "dclid",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "msclkid",
    }
)


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


# Project only arguments that can change capability execution; actor rationale is not payload.
def tool_request_execution_arguments(
    request: ToolRequest,
) -> tuple[tuple[str, str], ...]:
    if request.tool_name == ToolName.WEB_SEARCH:
        return (
            ("query", request.query or ""),
            ("max_results", str(request.max_results)),
        )
    if request.tool_name == ToolName.FETCH_URL_TEXT:
        return (("url", request.url or ""),)
    if request.tool_name == ToolName.CONTROLLED_DOCUMENT_READ:
        return (("document_id", request.document_id or ""),)
    if request.tool_name == ToolName.CONTROLLED_DATASET_READ:
        return (
            ("dataset_id", request.dataset_id or ""),
            ("limit", "" if request.limit is None else str(request.limit)),
        )
    if request.tool_name == ToolName.MCP_LOCAL_TEST_TEXT_STATS:
        return (
            ("text", request.text or ""),
            ("limit", "" if request.limit is None else str(request.limit)),
        )
    if request.tool_name == ToolName.MCP_LOCAL_TEST_ECHO:
        return (("text", request.text or ""),)
    if request.tool_name == ToolName.MCP_LOCAL_TEST_READ_FIXTURE:
        return (("path", request.path or ""),)
    if request.tool_name == ToolName.MCP_LOCAL_TEST_CALCULATE_EXPRESSION:
        return (("expression", request.expression or ""),)
    return ()


# Normalize a web locator for operational identity and evidence equivalence only.
def canonical_web_locator(locator: str) -> str:
    stripped = locator.strip()
    parsed = urlsplit(stripped)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return stripped

    scheme = parsed.scheme.casefold()
    hostname = (parsed.hostname or "").casefold()
    try:
        port = parsed.port
    except ValueError:
        return stripped
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        hostname = f"{hostname}:{port}"

    # Tracking-only parameters do not change the fetched document or support a
    # different factual claim; every other query parameter remains significant.
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in _TRACKING_QUERY_KEYS
    ]
    normalized_query = urlencode(sorted(query_items), doseq=True)
    path = parsed.path
    if path == "/":
        path = ""
    elif path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, hostname, path, normalized_query, ""))


# Build a stable capability identity from normalized executable arguments only.
def canonical_tool_request_identity(request: ToolRequest) -> str:
    arguments = tool_request_execution_arguments(request)
    if request.tool_name in {
        ToolName.WEB_SEARCH,
        ToolName.CONTROLLED_DOCUMENT_READ,
        ToolName.CONTROLLED_DATASET_READ,
        ToolName.MCP_LOCAL_TEST_TEXT_STATS,
        ToolName.MCP_LOCAL_TEST_ECHO,
        ToolName.MCP_LOCAL_TEST_READ_FIXTURE,
        ToolName.MCP_LOCAL_TEST_CALCULATE_EXPRESSION,
    }:
        normalized = tuple(
            (
                name,
                value
                if name in {"limit", "max_results"}
                else " ".join(value.split()).casefold(),
            )
            for name, value in arguments
        )
    elif request.tool_name == ToolName.FETCH_URL_TEXT:
        normalized = tuple(
            (name, canonical_web_locator(value))
            for name, value in arguments
        )
    else:
        normalized = arguments
    return f"{request.tool_name.value}:{normalized!r}"


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
    "canonical_tool_request_identity",
    "canonical_web_locator",
    "tool_request_execution_arguments",
]
