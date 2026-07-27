from __future__ import annotations

from typing import TYPE_CHECKING, Any

from llm_retry import llm_retry

from ..contracts import CitationAlignment, EvidenceContentState, Extractability, ToolExecutionResult, ToolRequest, ToolSource
from ..models import OpenAIToolGatewaySettings, ToolDefinition
from ..results import failure_result
from ..text import truncate_text

if TYPE_CHECKING:
    from openai import OpenAI as OpenAIClient
else:
    OpenAIClient = Any

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - dependency is checked at execution time.
    OpenAI = None


class OpenAIWebSearchAdapter:
    # Keep provider lifecycle and response normalization outside gateway dispatch.
    def __init__(self, *, settings: OpenAIToolGatewaySettings) -> None:
        self._settings = settings
        self._client: OpenAIClient | None = None

    # Execute a real web search through the OpenAI provider adapter.
    def execute(
        self,
        *,
        tool_request: ToolRequest,
        definition: ToolDefinition,
    ) -> ToolExecutionResult:
        if OpenAI is None:
            return failure_result(tool_request, "The 'openai' package is not installed.")
        if not self._settings.api_key:
            return failure_result(tool_request, "OPENAI_API_KEY is missing for web_search.")
        if self._client is None:
            self._client = OpenAI(
                api_key=self._settings.api_key,
                timeout=self._settings.timeout_seconds,
            )
        try:
            response = self._request_response(tool_request=tool_request)
        except Exception as exc:  # pragma: no cover - provider errors are normalized.
            return failure_result(tool_request, f"Web search failed: {exc}")
        content = getattr(response, "output_text", None)
        return ToolExecutionResult(
            request=tool_request,
            success=True,
            summary="Web search executed successfully.",
            content=truncate_text(content, definition.max_content_chars) if content else None,
            sources=extract_web_sources(response),
        )

    # Retry only transient provider failures for stable governed search execution.
    @llm_retry
    def _request_response(self, *, tool_request: ToolRequest) -> object:
        return self._client.responses.create(
            model=self._settings.model,
            input=tool_request.query,
            tools=[{"type": "web_search_preview"}],
        )


# Extract citations or source links from a provider response where available.
def extract_web_sources(response: object) -> tuple[ToolSource, ...]:
    deduped: dict[str, ToolSource] = {}
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            for annotation in getattr(content, "annotations", []) or []:
                url = _annotation_value(annotation, "url")
                if not url:
                    continue
                title = _annotation_value(annotation, "title") or url
                snippet, alignment = _citation_snippet(content=content, annotation=annotation)
                deduped[url] = ToolSource(
                    title=title,
                    locator=url,
                    snippet=truncate_text(snippet, 300) if snippet else None,
                    content_state=(EvidenceContentState.PROVIDER_CITED_SUMMARY if snippet else EvidenceContentState.METADATA_ONLY),
                    extractability=(Extractability.COMPLETE if snippet else Extractability.UNAVAILABLE),
                    citation_alignment=alignment,
                )
    return tuple(deduped.values())


# Support both SDK objects and dict-like annotations defensively.
def _annotation_value(annotation: object, key: str) -> str | None:
    value = getattr(annotation, key, None)
    if value is not None:
        return str(value)
    if isinstance(annotation, dict):
        raw_value = annotation.get(key)
        return str(raw_value) if raw_value is not None else None
    return None


# Keep only the text actually tied to one citation; never copy the aggregate answer to every URL.
def _citation_snippet(*, content: object, annotation: object) -> tuple[str | None, CitationAlignment]:
    text = _annotation_value(content, "text") or ""
    start = _annotation_int(annotation, "start_index")
    end = _annotation_int(annotation, "end_index")
    if text and start is not None and end is not None and 0 <= start < end <= len(text):
        sentence = _containing_sentence(text=text, start=start, end=end)
        return sentence, CitationAlignment.EXACT
    attributed = _annotation_value(annotation, "text")
    if attributed:
        return attributed, CitationAlignment.PROVIDER_ATTRIBUTED
    return None, CitationAlignment.NONE


def _annotation_int(annotation: object, key: str) -> int | None:
    value = getattr(annotation, key, None)
    if value is None and isinstance(annotation, dict):
        value = annotation.get(key)
    return value if isinstance(value, int) else None


def _containing_sentence(*, text: str, start: int, end: int) -> str:
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    right_period = text.find(".", end)
    right_newline = text.find("\n", end)
    candidates = [value for value in (right_period, right_newline) if value >= 0]
    right = min(candidates) + 1 if candidates else len(text)
    return text[left:right].strip()


__all__ = ["OpenAIWebSearchAdapter", "extract_web_sources"]
