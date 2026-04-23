from __future__ import annotations

import os
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .tool_contract import (
    ToolExecutionResult,
    ToolGatewayPort,
    ToolName,
    ToolRequest,
    ToolSource,
)

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled at runtime if dependency is missing
    OpenAI = None


@dataclass(frozen=True)
class OpenAIToolGatewaySettings:
    model: str = "gpt-5.4-mini"
    api_key: str | None = None
    timeout_seconds: float = 30.0
    fetch_timeout_seconds: float = 20.0

    @classmethod
    def from_env(cls) -> "OpenAIToolGatewaySettings":
        # Merge process env and local .env files into one tool gateway settings object.
        env_file_values = _load_env_file_values()

        return cls(
            model=os.getenv(
                "LAYER2_TOOL_GATEWAY_OPENAI_MODEL",
                env_file_values.get("LAYER2_TOOL_GATEWAY_OPENAI_MODEL", "gpt-5.4-mini"),
            ),
            api_key=os.getenv("OPENAI_API_KEY", env_file_values.get("OPENAI_API_KEY")),
            timeout_seconds=float(
                os.getenv(
                    "LAYER2_TOOL_GATEWAY_OPENAI_TIMEOUT_SECONDS",
                    env_file_values.get("LAYER2_TOOL_GATEWAY_OPENAI_TIMEOUT_SECONDS", "30"),
                )
            ),
            fetch_timeout_seconds=float(
                os.getenv(
                    "LAYER2_TOOL_GATEWAY_FETCH_TIMEOUT_SECONDS",
                    env_file_values.get("LAYER2_TOOL_GATEWAY_FETCH_TIMEOUT_SECONDS", "20"),
                )
            ),
        )


class ToolExecutionError(RuntimeError):
    """Raised when the tool gateway cannot execute a valid request."""


class OpenAIToolGateway(ToolGatewayPort):
    gateway_name = "openai_tool_gateway"

    def __init__(self, settings: OpenAIToolGatewaySettings | None = None) -> None:
        # Validate tool gateway prerequisites once at construction time.
        self._settings = settings or OpenAIToolGatewaySettings.from_env()

        if OpenAI is None:
            raise RuntimeError(
                "The 'openai' package is not installed. Install it before using the tool gateway."
            )
        if not self._settings.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is missing. Set it in the process environment or in src/.env."
            )

        self._client = OpenAI(
            api_key=self._settings.api_key,
            timeout=self._settings.timeout_seconds,
        )

    # Execute one typed tool request against the current operational tool surface.
    def execute(self, *, tool_request: ToolRequest) -> ToolExecutionResult:
        _ensure_valid_tool_request(tool_request)

        if tool_request.tool_name == ToolName.WEB_SEARCH:
            return self._execute_web_search(tool_request=tool_request)

        if tool_request.tool_name == ToolName.FETCH_URL_TEXT:
            return self._execute_fetch_url_text(tool_request=tool_request)

        raise ToolExecutionError(f"Unsupported tool name: {tool_request.tool_name.value}")

    # Execute a real web search using the OpenAI web search tool.
    def _execute_web_search(self, *, tool_request: ToolRequest) -> ToolExecutionResult:
        try:
            response = self._client.responses.create(
                model=self._settings.model,
                input=tool_request.query,
                tools=[{"type": "web_search_preview"}],
            )
        except Exception as exc:  # pragma: no cover - network/client errors
            return ToolExecutionResult(
                request=tool_request,
                success=False,
                summary="Web search failed.",
                error_message=str(exc),
            )

        content = getattr(response, "output_text", None)
        sources = _extract_web_sources(response)

        return ToolExecutionResult(
            request=tool_request,
            success=True,
            summary="Web search executed successfully.",
            content=_truncate_text(content, 12000) if content else None,
            sources=sources,
        )

    # Fetch one URL and extract readable text using only the standard library.
    def _execute_fetch_url_text(self, *, tool_request: ToolRequest) -> ToolExecutionResult:
        try:
            request = Request(
                tool_request.url,
                headers={
                    "User-Agent": "Layer2ToolGateway/1.0",
                    "Accept": "text/html, text/plain;q=0.9, */*;q=0.1",
                },
            )
            with urlopen(request, timeout=self._settings.fetch_timeout_seconds) as response:
                raw_body = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                content_type = response.headers.get_content_type()
        except Exception as exc:  # pragma: no cover - network/io errors
            return ToolExecutionResult(
                request=tool_request,
                success=False,
                summary="URL fetch failed.",
                error_message=str(exc),
            )

        decoded_body = raw_body.decode(charset, errors="replace")

        # Convert HTML into readable text, but leave plain text mostly untouched.
        if content_type == "text/html":
            title = _extract_title(decoded_body) or tool_request.url
            extracted_text = _extract_visible_text(decoded_body)
        else:
            title = tool_request.url
            extracted_text = decoded_body

        content = _truncate_text(extracted_text, 12000)
        snippet = _truncate_text(content, 500)

        return ToolExecutionResult(
            request=tool_request,
            success=True,
            summary="URL text fetched successfully.",
            content=content,
            sources=(
                ToolSource(
                    title=title,
                    locator=tool_request.url,
                    snippet=snippet,
                ),
            ),
        )


class _VisibleTextExtractor(HTMLParser):
    # Strip HTML down to visible text while skipping obvious non-content blocks.
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    # Track ignored regions so script and style content never enters the text output.
    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip_depth += 1

    # Close ignored regions as the parser exits them.
    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1

    # Normalize visible text chunks aggressively to keep the output compact.
    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return

        text = " ".join(data.split())
        if text:
            self._chunks.append(text)

    # Return one compact plain-text body.
    def get_text(self) -> str:
        return " ".join(self._chunks)


# Validate the request before any external call is attempted.
def _ensure_valid_tool_request(tool_request: ToolRequest) -> None:
    if not tool_request.reason.strip():
        raise ToolExecutionError("ToolRequest.reason cannot be empty.")

    if tool_request.tool_name == ToolName.WEB_SEARCH:
        if not tool_request.query or not tool_request.query.strip():
            raise ToolExecutionError("web_search requires a non-empty query.")
        return

    if tool_request.tool_name == ToolName.FETCH_URL_TEXT:
        if not tool_request.url or not tool_request.url.strip():
            raise ToolExecutionError("fetch_url_text requires a non-empty url.")

        parsed = urlparse(tool_request.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ToolExecutionError("fetch_url_text requires a valid http or https URL.")
        return


# Extract citations or source links from a web search response where available.
def _extract_web_sources(response) -> tuple[ToolSource, ...]:
    deduped: dict[str, ToolSource] = {}

    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            for annotation in getattr(content, "annotations", []) or []:
                url = _annotation_value(annotation, "url")
                if not url:
                    continue

                title = _annotation_value(annotation, "title") or url
                snippet = _annotation_value(annotation, "text")
                deduped[url] = ToolSource(
                    title=title,
                    locator=url,
                    snippet=_truncate_text(snippet, 300) if snippet else None,
                )

    return tuple(deduped.values())


# Support both SDK objects and dict-like annotations defensively.
def _annotation_value(annotation, key: str) -> str | None:
    value = getattr(annotation, key, None)
    if value is not None:
        return value

    if isinstance(annotation, dict):
        return annotation.get(key)

    return None


# Extract a readable title from an HTML page when one exists.
def _extract_title(html_text: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return None

    title = unescape(match.group(1))
    title = " ".join(title.split())
    return title or None


# Convert HTML into compact visible text.
def _extract_visible_text(html_text: str) -> str:
    parser = _VisibleTextExtractor()
    parser.feed(html_text)
    parser.close()
    return parser.get_text()


# Keep tool content bounded so TaskState stays manageable.
def _truncate_text(text: str | None, limit: int) -> str | None:
    if text is None:
        return None

    if len(text) <= limit:
        return text

    return text[: limit - 3].rstrip() + "..."


def _load_env_file_values() -> dict[str, str]:
    # Load simple KEY=VALUE pairs from the supported .env file locations.
    env_values: dict[str, str] = {}

    for candidate in _candidate_env_paths():
        if not candidate.exists():
            continue

        for raw_line in candidate.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key:
                env_values[key] = value

    return env_values


def _candidate_env_paths() -> list[Path]:
    # Search both repository root and src-level env files for local development.
    repo_root = Path(__file__).resolve().parents[2]
    return [
        repo_root / ".env",
        repo_root / "src" / ".env",
    ]


# Build the production tool gateway used by the live Layer 2 runtime.
def build_default_tool_gateway() -> ToolGatewayPort:
    return OpenAIToolGateway()


__all__ = [
    "OpenAIToolGateway",
    "OpenAIToolGatewaySettings",
    "ToolExecutionError",
    "build_default_tool_gateway",
]
