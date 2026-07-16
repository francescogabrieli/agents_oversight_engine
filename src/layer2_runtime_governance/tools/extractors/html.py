from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser


class _VisibleTextExtractor(HTMLParser):
    # Strip HTML down to visible text while skipping non-content blocks.
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    # Track ignored regions so script and style content never enters output.
    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip_depth += 1

    # Close ignored regions as the parser exits them.
    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1

    # Normalize visible chunks so evidence payloads remain compact.
    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        text = " ".join(data.split())
        if text:
            self._chunks.append(text)

    # Return one compact plain-text body.
    def get_text(self) -> str:
        return " ".join(self._chunks)


# Extract a readable title from an HTML page when one exists.
def extract_title(html_text: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    title = " ".join(unescape(match.group(1)).split())
    return title or None


# Convert HTML into compact visible text.
def extract_visible_text(html_text: str) -> str:
    parser = _VisibleTextExtractor()
    parser.feed(html_text)
    parser.close()
    return parser.get_text()


__all__ = ["extract_title", "extract_visible_text"]
