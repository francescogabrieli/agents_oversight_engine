from __future__ import annotations

from io import BytesIO

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - optional dependency checked at execution time.
    PdfReader = None


# Extract visible text from a PDF without retaining layout metadata.
def extract_pdf_text(pdf_bytes: bytes) -> str:
    if PdfReader is None:
        return ""
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:  # pragma: no cover - malformed external PDF content.
        return ""


__all__ = ["extract_pdf_text"]
