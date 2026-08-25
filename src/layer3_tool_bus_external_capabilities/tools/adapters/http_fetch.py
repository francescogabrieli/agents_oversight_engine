from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from email.message import Message
from typing import Callable, Protocol
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from ..contracts import ToolErrorCode, ToolExecutionResult, ToolObservation, ToolRequest
from ..eu_legal import try_fetch_specialized_legal_document
from ..extractors import (
    extract_html_publication_timestamp,
    extract_pdf_text,
    extract_visible_text,
)
from ..models import ToolDefinition
from ..text import is_meaningful_extracted_text, truncate_text


class _HttpResponse(Protocol):
    headers: Message

    # Read one bounded response body.
    def read(self, amount: int = -1) -> bytes: ...  # noqa

    # Support urllib response context management.
    def __enter__(self) -> "_HttpResponse": ...

    # Preserve the standard response cleanup contract.
    def __exit__(self, _exc_type, _exc, _traceback) -> None: ...


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
        if isinstance(fetch_result, tuple) and len(fetch_result) == 2 and isinstance(fetch_result[1], ToolErrorCode):
            return ToolExecutionResult(request=tool_request, success=False, summary="URL fetch failed.", error_message=str(fetch_result[0]), error_code=fetch_result[1], extractability_limited=True)
        raw_body, charset, content_type, truncated, transport_timestamp = fetch_result
        if truncated and content_type == "application/pdf":
            return ToolExecutionResult(request=tool_request, success=False, summary="PDF extraction exceeded the byte limit.", error_message="PDF byte limit prevents treating the document as complete.", error_code=ToolErrorCode.BYTE_LIMIT, extractability_limited=True)
        decoded_body = raw_body.decode(charset, errors="replace")
        if content_type in {"text/html", "application/xhtml+xml"}:
            extracted_text = extract_visible_text(decoded_body)
            publication_timestamp = extract_html_publication_timestamp(decoded_body)
            source_timestamp = (
                publication_timestamp.value
                if publication_timestamp is not None
                else None
            )
        else:
            extracted_text = decoded_body
            publication_timestamp = None
            source_timestamp = None
        content = truncate_text(extracted_text, definition.max_content_chars)
        content_truncated = truncated or len(content or "") < len(extracted_text)
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
            return ToolExecutionResult(request=tool_request, success=False, summary="URL extraction was empty.", error_message="URL fetch succeeded but extracted readable text was empty.", error_code=ToolErrorCode.EMPTY_EXTRACTION, extractability_limited=True)
        return ToolExecutionResult(
            request=tool_request,
            success=True,
            summary=_source_timestamp_summary(
                publication_basis=(
                    publication_timestamp.basis
                    if publication_timestamp is not None
                    else None
                ),
                transport_timestamp=transport_timestamp,
            ),
            content=content,
            truncated=content_truncated,
            extractability_limited=content_truncated,
            observations=(
                ToolObservation(
                    observation_id=f"{tool_request.request_id}:document",
                    locator=tool_request.url or "",
                    content=content,
                    source_timestamp=source_timestamp,
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
    ) -> tuple[bytes, str, str, bool, datetime | None] | tuple[str, ToolErrorCode]:
        try:
            headers = {"User-Agent": "Layer2ToolGateway/1.0", "Accept": accept}
            if accept_language:
                headers["Accept-Language"] = accept_language
            request = Request(url, headers=headers)
            with self._opener(request, timeout=timeout_seconds) as response:
                raw_body = response.read(max_bytes + 1)
                charset = response.headers.get_content_charset() or "utf-8"
                content_type = response.headers.get_content_type()
                source_timestamp = _parse_last_modified(response.headers)
        except HTTPError as exc:
            return (f"HTTP {exc.code}", ToolErrorCode.FORBIDDEN if exc.code == 403 else ToolErrorCode.UNAVAILABLE)
        except TimeoutError as exc:
            return (str(exc), ToolErrorCode.TIMEOUT)
        except URLError as exc:  # pragma: no cover - network errors are normalized.
            message = str(exc)
            return (message, ToolErrorCode.BOT_CHALLENGE if "challenge" in message.lower() else ToolErrorCode.UNAVAILABLE)
        except Exception as exc:  # pragma: no cover - network errors are normalized.
            return (str(exc), ToolErrorCode.UNAVAILABLE)
        truncated = len(raw_body) > max_bytes
        # HTML/text remains useful as an explicitly partial prefix; never claim a truncated PDF is complete.
        return raw_body[:max_bytes], charset, content_type, truncated, source_timestamp


# Parse a source-provided Last-Modified header without inventing a retrieval timestamp.
def _parse_last_modified(headers: Message) -> datetime | None:
    get_header = getattr(headers, "get", None)
    if not callable(get_header):
        return None
    raw_value = get_header("Last-Modified")
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    try:
        parsed_value = parsedate_to_datetime(raw_value)
    except (IndexError, TypeError, ValueError):
        return None
    if parsed_value.tzinfo is None or parsed_value.utcoffset() is None:
        return None
    return parsed_value.astimezone(timezone.utc)


# Explain which source timestamp was accepted without treating transport metadata as publication.
def _source_timestamp_summary(
    *,
    publication_basis: str | None,
    transport_timestamp: datetime | None,
) -> str:
    if publication_basis is not None:
        return (
            "URL text fetched successfully. Publication timestamp extracted from "
            f"{publication_basis}."
        )
    if transport_timestamp is not None:
        return (
            "URL text fetched successfully. HTTP Last-Modified was observed but "
            "excluded from semantic publication freshness."
        )
    return "URL text fetched successfully."


# Resolve urllib at call time so tests and deployments can inject transport cleanly.
def _open_url(request: Request, *, timeout: float) -> _HttpResponse:
    return urlopen(request, timeout=timeout)


# Resolve the optional PDF extractor at call time for explicit adapter substitution.
def _extract_pdf(pdf_bytes: bytes) -> str:
    return extract_pdf_text(pdf_bytes)


__all__ = ["HttpFetchAdapter"]
