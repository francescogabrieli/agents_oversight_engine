from __future__ import annotations

from email.message import Message
from typing import Callable, Protocol
from urllib.request import Request, urlopen

from ..contracts import ToolExecutionResult, ToolRequest, ToolSource
from ..extractors import (
    build_cellar_fallback_url,
    celex_identifier,
    extract_pdf_text,
    extract_title,
    extract_visible_text,
    is_eur_lex_bot_challenge,
)
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
        if (
            not is_meaningful_extracted_text(content)
            or is_eur_lex_bot_challenge(url=tool_request.url, extracted_text=extracted_text)
        ):
            fallback = self._try_eur_lex_fallback(
                tool_request=tool_request,
                definition=definition,
            )
            if fallback is not None:
                return fallback
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

    # Attempt the official CELLAR fallback for EUR-Lex pages with no usable text.
    def _try_eur_lex_fallback(
        self,
        *,
        tool_request: ToolRequest,
        definition: ToolDefinition,
    ) -> ToolExecutionResult | None:
        fallback_url = build_cellar_fallback_url(tool_request.url)
        if fallback_url is None:
            return None
        fallback_result = self._fetch_document(
            url=fallback_url,
            timeout_seconds=definition.timeout_seconds,
            max_bytes=max(definition.max_bytes, 2_000_000),
            accept="application/pdf",
            accept_language="eng",
        )
        if isinstance(fallback_result, str):
            return None
        body, charset, content_type = fallback_result
        fallback_text = (
            self._pdf_extractor(body)
            if content_type == "application/pdf"
            else extract_visible_text(body.decode(charset, errors="replace"))
        )
        content = truncate_text(fallback_text, definition.max_content_chars)
        if not is_meaningful_extracted_text(content):
            return None
        return ToolExecutionResult(
            request=tool_request,
            success=True,
            summary="EUR-Lex text fetched through the official CELLAR fallback.",
            content=content,
            sources=(
                ToolSource(
                    title=f"EUR-Lex CELEX content ({celex_identifier(tool_request.url)})",
                    locator=fallback_url,
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
