"""Bounded content extractors used by governed read-only tool adapters."""

from .eur_lex import build_cellar_fallback_url, celex_identifier, is_eur_lex_bot_challenge
from .html import extract_title, extract_visible_text
from .pdf import extract_pdf_text

__all__ = [
    "build_cellar_fallback_url",
    "celex_identifier",
    "extract_pdf_text",
    "extract_title",
    "extract_visible_text",
    "is_eur_lex_bot_challenge",
]
