from __future__ import annotations

from typing import Callable

from ...tool_contract import ToolExecutionResult, ToolObservation, ToolRequest
from ..models import ToolDefinition
from ..text import is_meaningful_extracted_text, truncate_text
from ..extractors import extract_visible_text
from .source import build_cellar_fallback_url, is_bot_challenge

FetchDocument = Callable[..., object]
PdfExtractor = Callable[[bytes], str]


# Recover supported official legal documents while keeping source logic outside core runtime.
def try_fetch_specialized_legal_document(
    *,
    tool_request: ToolRequest,
    definition: ToolDefinition,
    extracted_text: str,
    fetch_document: FetchDocument,
    pdf_extractor: PdfExtractor,
) -> ToolExecutionResult | None:
    fallback_url = build_cellar_fallback_url(tool_request.url)
    if fallback_url is None:
        return None
    if is_meaningful_extracted_text(extracted_text) and not is_bot_challenge(
        url=tool_request.url,
        extracted_text=extracted_text,
    ):
        return None
    fallback_result = fetch_document(
        url=fallback_url,
        timeout_seconds=definition.timeout_seconds,
        max_bytes=max(definition.max_bytes, 2_000_000),
        accept="application/pdf",
        accept_language="eng",
    )
    if not isinstance(fallback_result, tuple) or len(fallback_result) != 4:
        return None
    body, charset, content_type, truncated = fallback_result
    if not isinstance(body, bytes) or truncated:
        return None
    fallback_text = (
        pdf_extractor(body)
        if content_type == "application/pdf"
        else extract_visible_text(body.decode(charset, errors="replace"))
    )
    content = truncate_text(fallback_text, definition.max_content_chars)
    content_truncated = len(content or "") < len(fallback_text)
    if not is_meaningful_extracted_text(content):
        return None
    return ToolExecutionResult(
        request=tool_request,
        success=True,
        summary="Official EU legal text fetched through the publication fallback.",
        content=content,
        truncated=content_truncated,
        extractability_limited=content_truncated,
        observations=(
            ToolObservation(
                observation_id=f"{tool_request.request_id}:document",
                locator=fallback_url,
                content=content,
            ),
        ),
    )


__all__ = ["try_fetch_specialized_legal_document"]
