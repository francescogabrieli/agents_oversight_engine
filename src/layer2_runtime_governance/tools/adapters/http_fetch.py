from __future__ import annotations

from email.message import Message
from typing import Callable, Protocol
from urllib.request import Request, urlopen

from ..contracts import ToolExecutionResult, ToolRequest, ToolSource
from ..eu_legal import try_fetch_specialized_legal_document
from ..extractors import extract_pdf_text, extract_title, extract_visible_text
from ..models import ToolDefinition
from ..results import failure_result
from ..text import is_meaningful_extracted_text, truncate_text


class _HttpResponse(Protocol):
    headers: Message

    # Read one bounded response body.
    def read(self, amount: int = -1) -> bytes: ...

    # Support urllib response context management.
    def __enter__(self) -> "_HttpResponse": ...

    # Preserve the standard response cleanup contract.
    def __exit__(self, exc_type, exc, traceback) -> None: ...


HttpOpener = Callable[..., _HttpResponse]
PdfExtractor = Callable[[bytes], str]


class HttpFetchAdapter:
    # Own bounded HTTP fetching and content extraction for the URL tool.
    def __init__(
        self,
        *,
        opener: HttpOpener | None = None,
        pdf_extractor: PdfExtractor | None = None,
    ) -> None:
        self._opener = opener or _open_url
        self._pdf_extractor = pdf_extractor or _extract_pdf

    # Fetch a URL and normalize readable content into the tool result contract.
    def execute(
        self,
        *,
        tool_request: ToolRequest,
        definition: ToolDefinition,
    ) -> ToolExecutionResult:
        fetch_result = self._fetch_document(
            url=tool_request.url or "",
            timeout_seconds=definition.timeout_seconds,
            max_bytes=definition.max_bytes,
            accept="text/html, text/plain;q=0.9, */*;q=0.1",
        )
        if isinstance(fetch_result, str):
            return failure_result(tool_request, f"URL fetch failed: {fetch_result}")
        raw_body, charset, content_type = fetch_result
        decoded_body = raw_body.decode(charset, errors="replace")
        if content_type == "text/html":
            title = extract_title(decoded_body) or tool_request.url
            extracted_text = extract_visible_text(decoded_body)
        else:
            title = tool_request.url
            extracted_text = decoded_body
        content = truncate_text(extracted_text, definition.max_content_chars)
        specialized_result = try_fetch_specialized_legal_document(
            tool_request=tool_request,
            definition=definition,
            extracted_text=extracted_text,
            fetch_document=self._fetch_document,
            pdf_extractor=self._pdf_extractor,
        )
        if specialized_result is not None:
            return specialized_result
        if not is_meaningful_extracted_text(content):
            return failure_result(
                tool_request,
                "URL fetch succeeded but extracted readable text was empty.",
            )
        return ToolExecutionResult(
            request=tool_request,
            success=True,
            summary="URL text fetched successfully.",
            content=content,
            sources=(
                ToolSource(
                    title=title,
                    locator=tool_request.url or "",
                    snippet=truncate_text(content, 500),
                ),
            ),
        )

    # Read one bounded public HTTP document with explicit auditable headers.
    def _fetch_document(
        self,
        *,
        url: str,
        timeout_seconds: float,
        max_bytes: int,
        accept: str,
        accept_language: str | None = None,
    ) -> tuple[bytes, str, str] | str:
        try:
            headers = {"User-Agent": "Layer2ToolGateway/1.0", "Accept": accept}
            if accept_language:
                headers["Accept-Language"] = accept_language
            request = Request(url, headers=headers)
            with self._opener(request, timeout=timeout_seconds) as response:
                raw_body = response.read(max_bytes + 1)
                charset = response.headers.get_content_charset() or "utf-8"
                content_type = response.headers.get_content_type()
        except Exception as exc:  # pragma: no cover - network errors are normalized.
            return str(exc)
        if len(raw_body) > max_bytes:
            return "URL fetch exceeded the configured byte limit."
        return raw_body, charset, content_type


# Resolve urllib at call time so tests and deployments can inject transport cleanly.
def _open_url(request: Request, *, timeout: float) -> _HttpResponse:
    return urlopen(request, timeout=timeout)


# Resolve the optional PDF extractor at call time for explicit adapter substitution.
def _extract_pdf(pdf_bytes: bytes) -> str:
    return extract_pdf_text(pdf_bytes)


__all__ = ["HttpFetchAdapter"]
