from __future__ import annotations


# Keep text bounded so provider and runtime state payloads remain manageable.
def truncate_text(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


# Treat tiny title-only pages as empty extraction for evidence purposes.
def is_meaningful_extracted_text(text: str | None) -> bool:
    if text is None:
        return False
    return len(" ".join(text.split())) >= 40


__all__ = ["is_meaningful_extracted_text", "truncate_text"]
