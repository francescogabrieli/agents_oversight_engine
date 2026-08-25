from __future__ import annotations

import re
from html import escape

# A deliberately small, deterministic renderer for the Markdown subset agent briefings
# actually use: ATX headings, bold, inline code, bullet and ordered lists, links, rules.
# It is a presentation transform only. The canonical body remains the text/plain part of
# the message, so the rendered alternative can never carry content the reviewer did not
# approve. Anything the renderer does not recognize survives as escaped literal text.

_HEADING = re.compile(r"^(?P<level>#{1,6})\s+(?P<text>.+?)\s*#*$")
_BULLET = re.compile(r"^\s*[-*+]\s+(?P<text>.+)$")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+(?P<text>.+)$")
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_BOLD = re.compile(r"\*\*(?P<text>.+?)\*\*", re.DOTALL)
_ITALIC = re.compile(r"(?<![\w*])\*(?P<text>[^*\n]+?)\*(?![\w*])")
_CODE = re.compile(r"`(?P<text>[^`\n]+?)`")
_LINK = re.compile(r"\[(?P<label>[^\]\n]+)\]\((?P<href>[^)\s]+)\)")
_BARE_URL = re.compile(r"(?<![\"'=>])\bhttps?://[^\s<>\"'()]+")

_STYLE = (
    "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
    "font-size:14px;line-height:1.55;color:#1f2328;max-width:720px;"
)


# Only http(s) targets become links; anything else stays inert escaped text.
def _safe_href(href: str) -> str | None:
    normalized = href.strip()
    if normalized.lower().startswith(("http://", "https://")):
        return escape(normalized, quote=True)
    return None


def _inline(text: str) -> str:
    rendered = escape(text)
    rendered = _CODE.sub(
        lambda m: f"<code style=\"background:#f0f1f3;padding:1px 4px;border-radius:3px\">{m.group('text')}</code>",
        rendered,
    )
    rendered = _BOLD.sub(lambda m: f"<strong>{m.group('text')}</strong>", rendered)
    rendered = _ITALIC.sub(lambda m: f"<em>{m.group('text')}</em>", rendered)

    def _link(match: re.Match[str]) -> str:
        href = _safe_href(match.group("href"))
        if href is None:
            return match.group(0)
        return f'<a href="{href}">{match.group("label")}</a>'

    rendered = _LINK.sub(_link, rendered)

    def _autolink(match: re.Match[str]) -> str:
        href = _safe_href(match.group(0))
        if href is None:
            return match.group(0)
        return f'<a href="{href}">{href}</a>'

    return _BARE_URL.sub(_autolink, rendered)


# Render the approved Markdown body into a self-contained HTML alternative part.
def render_markdown_email_html(body: str) -> str:
    parts: list[str] = []
    list_tag: str | None = None

    def _close_list() -> None:
        nonlocal list_tag
        if list_tag is not None:
            parts.append(f"</{list_tag}>")
            list_tag = None

    def _open_list(tag: str) -> None:
        nonlocal list_tag
        if list_tag != tag:
            _close_list()
            parts.append(f'<{tag} style="margin:8px 0 8px 22px;padding:0">')
            list_tag = tag

    for raw_line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.rstrip()
        if not line.strip():
            _close_list()
            continue
        if _RULE.match(line):
            _close_list()
            parts.append('<hr style="border:0;border-top:1px solid #d8dade;margin:16px 0">')
            continue
        heading = _HEADING.match(line.strip())
        if heading is not None:
            _close_list()
            level = min(len(heading.group("level")), 6)
            size = {1: 20, 2: 17, 3: 15}.get(level, 14)
            parts.append(
                f'<h{level} style="font-size:{size}px;margin:18px 0 6px;font-weight:600">'
                f'{_inline(heading.group("text"))}</h{level}>'
            )
            continue
        bullet = _BULLET.match(line)
        if bullet is not None:
            _open_list("ul")
            parts.append(f'<li style="margin:3px 0">{_inline(bullet.group("text"))}</li>')
            continue
        ordered = _ORDERED.match(line)
        if ordered is not None:
            _open_list("ol")
            parts.append(f'<li style="margin:3px 0">{_inline(ordered.group("text"))}</li>')
            continue
        _close_list()
        parts.append(f'<p style="margin:8px 0">{_inline(line.strip())}</p>')

    _close_list()
    return (
        "<!doctype html><html><body>"
        f'<div style="{_STYLE}">{"".join(parts)}</div>'
        "</body></html>"
    )


__all__ = ["render_markdown_email_html"]
