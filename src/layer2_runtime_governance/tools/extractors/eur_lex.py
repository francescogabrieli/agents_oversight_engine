from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse


# Convert an official EUR-Lex CELEX URL into the CELLAR content endpoint.
def build_cellar_fallback_url(url: str | None) -> str | None:
    celex = celex_identifier(url)
    if celex is None:
        return None
    base_celex = celex
    consolidated_match = re.fullmatch(r"0(\d{4}[A-Z]\d+)(?:-\d+)?", celex)
    if consolidated_match is not None:
        base_celex = f"3{consolidated_match.group(1)}"
    return f"http://publications.europa.eu/resource/celex/{base_celex}"


# Extract a CELEX identifier only from a validated EUR-Lex URL.
def celex_identifier(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() != "eur-lex.europa.eu":
        return None
    uri_values = parse_qs(parsed.query).get("uri", ())
    if not uri_values:
        return None
    candidate = unquote(uri_values[0]).upper()
    match = re.search(r"(?:^|:)CELEX:([0-9A-Z-]+)", candidate)
    return match.group(1) if match is not None else None


# Detect EUR-Lex anti-bot interstitials before they become observed evidence.
def is_eur_lex_bot_challenge(*, url: str | None, extracted_text: str) -> bool:
    if celex_identifier(url) is None:
        return False
    normalized = " ".join(extracted_text.lower().split())
    return (
        "javascript is disabled" in normalized
        or "verify that you're not a robot" in normalized
        or "verify that you are not a robot" in normalized
    )


__all__ = ["build_cellar_fallback_url", "celex_identifier", "is_eur_lex_bot_challenge"]
