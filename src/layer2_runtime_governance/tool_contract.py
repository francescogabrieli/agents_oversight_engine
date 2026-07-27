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


class EvidenceContentState(str, Enum):
    # Content state records the concrete amount of source material actually observed.
    METADATA_ONLY = "metadata_only"
    PROVIDER_CITED_SUMMARY = "provider_cited_summary"
    EXTRACTED_PARTIAL = "extracted_partial"
    EXTRACTED_FULL = "extracted_full"


class SourceAuthority(str, Enum):
    # Authority is descriptive and deliberately has no universal ordering.
    OFFICIAL_PRIMARY = "official_primary"
    PRIMARY = "primary"
    SECONDARY = "secondary"
    UNKNOWN = "unknown"


class DocumentType(str, Enum):
    PRODUCT_ANNOUNCEMENT = "product_announcement"
    DOCUMENTATION = "documentation"
    LEGAL_TEXT = "legal_text"
    COMMUNITY_POST = "community_post"
    UNKNOWN = "unknown"


class Extractability(str, Enum):
    COMPLETE = "complete"
    TRUNCATED = "truncated"
    FORBIDDEN = "forbidden"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"


class CitationAlignment(str, Enum):
    EXACT = "exact"
    PROVIDER_ATTRIBUTED = "provider_attributed"
    NONE = "none"


class ToolErrorCode(str, Enum):
    FORBIDDEN = "forbidden"
    BYTE_LIMIT = "byte_limit"
    BOT_CHALLENGE = "bot_challenge"
    EMPTY_EXTRACTION = "empty_extraction"
    TIMEOUT = "timeout"
    CIRCUIT_OPEN = "circuit_open"
    DUPLICATE_REQUEST = "duplicate_request"
    UNAVAILABLE = "unavailable"


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
    content_state: EvidenceContentState = EvidenceContentState.METADATA_ONLY
    source_authority: SourceAuthority = SourceAuthority.UNKNOWN
    document_type: DocumentType = DocumentType.UNKNOWN
    extractability: Extractability = Extractability.UNAVAILABLE
    citation_alignment: CitationAlignment = CitationAlignment.NONE


@dataclass(frozen=True)
class ToolExecutionResult:
    # Tool execution always returns a structured result, including failures.
    request: ToolRequest
    success: bool
    summary: str
    content: str | None = None
    sources: tuple[ToolSource, ...] = ()
    error_message: str | None = None
    error_code: ToolErrorCode | None = None
    truncated: bool = False
    extractability_limited: bool = False


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
    content_state: EvidenceContentState = EvidenceContentState.METADATA_ONLY
    source_authority: SourceAuthority = SourceAuthority.UNKNOWN
    document_type: DocumentType = DocumentType.UNKNOWN
    extractability: Extractability = Extractability.UNAVAILABLE
    citation_alignment: CitationAlignment = CitationAlignment.NONE


class ToolGatewayPort(Protocol):
    # The gateway port decouples runtime orchestration from the concrete tool backend.
    gateway_name: str

    def execute(self, *, tool_request: ToolRequest) -> ToolExecutionResult: ...


__all__ = [
    "EvidenceSourceKind",
    "EvidenceContentState",
    "SourceAuthority",
    "DocumentType",
    "Extractability",
    "CitationAlignment",
    "ToolErrorCode",
    "ObservedEvidence",
    "ToolExecutionResult",
    "ToolGatewayPort",
    "ToolName",
    "ToolRequest",
    "ToolSource",
]
