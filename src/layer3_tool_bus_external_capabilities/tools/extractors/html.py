from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any


_META_PUBLICATION_KEYS = frozenset(
    {
        "article:published_time",
        "datepublished",
        "date_published",
        "dc.date.issued",
        "dcterms.issued",
        "og:published_time",
        "parsely-pub-date",
        "publication_date",
        "publish_date",
        "publishdate",
        "sailthru.date",
    }
)
_MONTH_NUMBERS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_MONTH_DATE_PATTERN = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,\s*|\s+)\d{4}\b",
    re.IGNORECASE,
)
_DAY_MONTH_DATE_PATTERN = re.compile(
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+"
    r"(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"\s+\d{4}\b",
    re.IGNORECASE,
)
_ISO_DATE_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}:?\d{2})?)?\b"
)
_PUBLISHED_ELEMENT_PATTERN = re.compile(
    r"^\s*published\s+(?:on\s+)?(?P<date>"
    + _MONTH_DATE_PATTERN.pattern.removeprefix(r"\b").removesuffix(r"\b")
    + r"|"
    + _DAY_MONTH_DATE_PATTERN.pattern.removeprefix(r"\b").removesuffix(r"\b")
    + r"|"
    + _ISO_DATE_PATTERN.pattern.removeprefix(r"\b").removesuffix(r"\b")
    + r")\s*$",
    re.IGNORECASE,
)
@dataclass(frozen=True)
class HtmlPublicationTimestamp:
    # Keep the selected semantic source visible to the HTTP adapter's audit summary.
    value: datetime
    basis: str


@dataclass(frozen=True)
class _TimestampCandidate:
    value: datetime
    basis: str
    priority: int


class _PublicationTimestampExtractor(HTMLParser):
    # Collect only publication-labelled metadata and visible date markers.
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.candidates: list[_TimestampCandidate] = []
        self._json_ld_chunks: list[list[str]] = []
        self._active_json_ld: list[str] | None = None
        self._semantic_elements: list[tuple[str, list[str]]] = []
        self._publication_label_elements: list[tuple[str, list[str]]] = []
        self._ignored_depth = 0

    # Inspect metadata attributes before visible-text extraction removes them.
    def handle_starttag(self, tag: str, attrs) -> None:
        normalized_tag = tag.casefold()
        attributes = {
            str(name).casefold(): str(value)
            for name, value in attrs
            if value is not None
        }
        if normalized_tag in {"style", "noscript"}:
            self._ignored_depth += 1
        if normalized_tag == "script":
            if attributes.get("type", "").split(";", 1)[0].strip().casefold() == "application/ld+json":
                self._active_json_ld = []
            else:
                self._ignored_depth += 1
        if normalized_tag == "meta":
            self._collect_meta_candidate(attributes)
        if normalized_tag == "time" and attributes.get("datetime"):
            marker = _semantic_date_marker(attributes)
            if marker:
                self._add_candidate(
                    attributes["datetime"],
                    basis=f"html_time:{marker}",
                    priority=30,
                )
        marker = _semantic_date_marker(attributes)
        if marker and normalized_tag not in {"meta", "link", "input", "time"}:
            self._semantic_elements.append((normalized_tag, []))
        if _is_publication_label_container(normalized_tag, attributes):
            self._publication_label_elements.append((normalized_tag, []))

    # Finalize JSON-LD and marked-element candidates at their structural boundary.
    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag == "script":
            if self._active_json_ld is not None:
                self._json_ld_chunks.append(self._active_json_ld)
                self._active_json_ld = None
            elif self._ignored_depth > 0:
                self._ignored_depth -= 1
        elif normalized_tag in {"style", "noscript"} and self._ignored_depth > 0:
            self._ignored_depth -= 1
        for index in range(len(self._semantic_elements) - 1, -1, -1):
            element_tag, chunks = self._semantic_elements[index]
            if element_tag != normalized_tag:
                continue
            self._semantic_elements.pop(index)
            self._add_candidate(
                " ".join(chunks),
                basis="html_element:semantic_date_class",
                priority=30,
            )
            break
        for index in range(len(self._publication_label_elements) - 1, -1, -1):
            element_tag, chunks = self._publication_label_elements[index]
            if element_tag != normalized_tag:
                continue
            self._publication_label_elements.pop(index)
            match = _PUBLISHED_ELEMENT_PATTERN.fullmatch(" ".join(chunks).strip())
            if match is not None:
                self._add_candidate(
                    match.group("date"),
                    basis="html_element:published_label",
                    priority=30,
                )
            break

    # Capture JSON-LD and text belonging to an explicitly publication-marked element.
    def handle_data(self, data: str) -> None:
        if self._active_json_ld is not None:
            self._active_json_ld.append(data)
            return
        if self._ignored_depth > 0:
            return
        for _tag, chunks in self._semantic_elements:
            chunks.append(data)
        for _tag, chunks in self._publication_label_elements:
            chunks.append(data)

    # Parse each JSON-LD block after HTMLParser has delivered its complete content.
    def close(self) -> None:
        super().close()
        for chunks in self._json_ld_chunks:
            try:
                payload = json.loads("".join(chunks))
            except (TypeError, ValueError):
                continue
            for raw_value in _json_ld_publication_values(payload):
                self._add_candidate(
                    raw_value,
                    basis="html_json_ld:datePublished",
                    priority=20,
                )

    # Accept only recognized publication metadata keys.
    def _collect_meta_candidate(self, attributes: dict[str, str]) -> None:
        metadata_key = next(
            (
                attributes[name].strip().casefold()
                for name in ("property", "name", "itemprop")
                if attributes.get(name)
            ),
            "",
        )
        if metadata_key not in _META_PUBLICATION_KEYS:
            return
        self._add_candidate(
            attributes.get("content", ""),
            basis=f"html_meta:{metadata_key}",
            priority=10,
        )

    # Normalize one candidate immediately so malformed metadata cannot affect precedence.
    def _add_candidate(self, raw_value: str, *, basis: str, priority: int) -> None:
        parsed = _parse_publication_datetime(raw_value)
        if parsed is not None:
            self.candidates.append(_TimestampCandidate(parsed, basis, priority))


class _VisibleTextExtractor(HTMLParser):
    # Strip HTML down to visible text while skipping non-content blocks.
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    # Track ignored regions so script and style content never enters output.
    def handle_starttag(self, tag: str, _attrs) -> None:
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


# Convert HTML into compact visible text.
def extract_visible_text(html_text: str) -> str:
    parser = _VisibleTextExtractor()
    parser.feed(html_text)
    parser.close()
    return parser.get_text()


# Extract one deterministic semantic publication timestamp, rejecting same-tier conflicts.
def extract_html_publication_timestamp(
    html_text: str,
) -> HtmlPublicationTimestamp | None:
    parser = _PublicationTimestampExtractor()
    parser.feed(html_text)
    parser.close()
    for priority in sorted({item.priority for item in parser.candidates}):
        candidates = [item for item in parser.candidates if item.priority == priority]
        distinct_values = {item.value for item in candidates}
        if len(distinct_values) != 1:
            return None
        selected = candidates[0]
        return HtmlPublicationTimestamp(value=selected.value, basis=selected.basis)
    return None


# Recursively find explicit schema.org datePublished values, including @graph payloads.
def _json_ld_publication_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).casefold() == "datepublished" and isinstance(nested, str):
                values.append(nested)
            else:
                values.extend(_json_ld_publication_values(nested))
    elif isinstance(value, list):
        for nested in value:
            values.extend(_json_ld_publication_values(nested))
    return values


# Recognize classes and item properties that semantically identify a publication date.
def _semantic_date_marker(attributes: dict[str, str]) -> str | None:
    itemprop = attributes.get("itemprop", "").strip().casefold()
    if itemprop == "datepublished":
        return "itemprop=datePublished"
    if "pubdate" in attributes:
        return "pubdate"
    marker_text = " ".join(
        attributes.get(name, "").casefold() for name in ("class", "id")
    )
    if any(
        hint in marker_text
        for hint in ("update", "modified", "modification", "revision", "lastmod")
    ):
        return None
    if "date" in marker_text and any(
        hint in marker_text
        for hint in ("article", "blog", "entry", "post", "publish", "publication")
    ):
        return "semantic_date_class"
    return None


# Scope the plain Published-label fallback to one metadata-like HTML element.
def _is_publication_label_container(
    tag: str,
    attributes: dict[str, str],
) -> bool:
    if tag not in {"div", "p", "span"}:
        return False
    marker_text = " ".join(
        attributes.get(name, "").casefold() for name in ("class", "id")
    )
    if any(
        hint in marker_text
        for hint in ("update", "modified", "modification", "revision", "lastmod")
    ):
        return False
    return any(
        hint in marker_text for hint in ("date", "meta", "publish", "publication")
    )


# Parse standardized metadata and explicit English publication labels without locale guessing.
def _parse_publication_datetime(raw_value: str) -> datetime | None:
    value = " ".join(raw_value.strip().split())
    if not value:
        return None
    iso_match = _ISO_DATE_PATTERN.search(value)
    if iso_match is not None:
        iso_value = iso_match.group(0)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso_value):
            try:
                return datetime.strptime(iso_value, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                return None
        try:
            parsed = datetime.fromisoformat(
                iso_value.replace("Z", "+00:00").replace("z", "+00:00")
            )
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    human_match = _MONTH_DATE_PATTERN.search(value) or _DAY_MONTH_DATE_PATTERN.search(value)
    if human_match is None:
        return None
    human_value = re.sub(
        r"(?<=\d)(?:st|nd|rd|th)\b",
        "",
        human_match.group(0),
        flags=re.IGNORECASE,
    ).replace(",", "")
    return _parse_english_calendar_date(human_value)


# Convert a publication-labelled English date without depending on process locale.
def _parse_english_calendar_date(value: str) -> datetime | None:
    parts = value.casefold().split()
    if len(parts) != 3:
        return None
    if parts[0] in _MONTH_NUMBERS:
        month_text, day_text, year_text = parts
    else:
        day_text, month_text, year_text = parts
    month = _MONTH_NUMBERS.get(month_text)
    if month is None:
        return None
    try:
        return datetime(
            int(year_text),
            month,
            int(day_text),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


__all__ = [
    "HtmlPublicationTimestamp",
    "extract_html_publication_timestamp",
    "extract_visible_text",
]
