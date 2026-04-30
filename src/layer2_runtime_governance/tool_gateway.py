from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .tool_contract import (
    ToolExecutionResult,
    ToolGatewayPort,
    ToolName,
    ToolRequest,
    ToolSource,
)

if TYPE_CHECKING:
    from openai import OpenAI as OpenAIClient
else:
    OpenAIClient = Any

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled at runtime if dependency is missing
    OpenAI = None


# ToolBackend names the concrete execution mechanism hidden behind ToolRequest.
class ToolBackend(str, Enum):
    OPENAI = "openai"
    HTTP_FETCH = "http_fetch"
    MCP = "mcp"
    LOCAL_RUNTIME = "local_runtime"


# ToolCapability keeps write-like tools out of the current baseline.
class ToolCapability(str, Enum):
    READ = "read"
    WRITE = "write"
    EXTERNAL_EFFECT = "external_effect"


# ToolRiskLevel records a coarse governance label for future policy use.
class ToolRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class McpServerDefinition:
    # MCP servers are configured explicitly so there is no uncontrolled discovery.
    server_id: str
    enabled: bool
    command: str
    args: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    timeout_seconds: float


@dataclass(frozen=True)
class ToolDefinition:
    # Tool definitions are the gateway-local registry consumed before execution.
    tool_name: ToolName
    backend: ToolBackend
    capability: ToolCapability
    risk_level: ToolRiskLevel
    enabled: bool
    requires_human_review: bool
    timeout_seconds: float
    required_fields: tuple[str, ...] = ()
    max_content_chars: int = 12000
    max_bytes: int = 200000
    allowed_schemes: tuple[str, ...] = ("https",)
    mcp_server_id: str | None = None
    mcp_tool_name: str | None = None


@dataclass(frozen=True)
class ToolGatewayConfig:
    # ToolGatewayConfig is the typed registry loaded from JSON configuration.
    tools: dict[ToolName, ToolDefinition]
    mcp_servers: dict[str, McpServerDefinition] = field(default_factory=dict)


@dataclass(frozen=True)
class OpenAIToolGatewaySettings:
    model: str = "gpt-5.4-nano"
    api_key: str | None = None
    timeout_seconds: float = 30.0
    fetch_timeout_seconds: float = 20.0
    config_path: Path | None = None

    @classmethod
    def from_env(cls) -> "OpenAIToolGatewaySettings":
        # Merge process env and local .env files into one tool gateway settings object.
        env_file_values = _load_env_file_values()

        config_value = os.getenv(
            "LAYER2_TOOL_GATEWAY_CONFIG_PATH",
            env_file_values.get("LAYER2_TOOL_GATEWAY_CONFIG_PATH", ""),
        )

        return cls(
            model=os.getenv(
                "LAYER2_TOOL_GATEWAY_OPENAI_MODEL",
                env_file_values.get("LAYER2_TOOL_GATEWAY_OPENAI_MODEL", "gpt-5.4-nano"),
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
            config_path=Path(config_value) if config_value else None,
        )


class ToolExecutionError(RuntimeError):
    """Raised when the tool gateway cannot execute a valid request."""


class OpenAIToolGateway(ToolGatewayPort):
    gateway_name = "openai_tool_gateway"

    def __init__(
        self,
        settings: OpenAIToolGatewaySettings | None = None,
        config: ToolGatewayConfig | None = None,
    ) -> None:
        # Construction stays offline-friendly; OpenAI is required only for web_search.
        self._settings = settings or OpenAIToolGatewaySettings.from_env()
        self._config = config or load_tool_gateway_config(
            config_path=self._settings.config_path,
        )
        self._client: OpenAIClient | None = None

    # Execute one typed tool request against the configured operational tool surface.
    def execute(self, *, tool_request: ToolRequest) -> ToolExecutionResult:
        definition = self._definition_for_request(tool_request=tool_request)
        if definition is None:
            return _failure(tool_request, "Tool is not registered or is disabled.")

        validation_error = _validate_request(
            tool_request=tool_request,
            definition=definition,
            config=self._config,
        )
        if validation_error is not None:
            return _failure(tool_request, validation_error)

        if definition.backend == ToolBackend.OPENAI:
            return self._execute_web_search(tool_request=tool_request, definition=definition)

        if definition.backend == ToolBackend.HTTP_FETCH:
            return self._execute_fetch_url_text(tool_request=tool_request, definition=definition)

        if definition.backend == ToolBackend.MCP:
            return _execute_mcp_tool(
                tool_request=tool_request,
                definition=definition,
                config=self._config,
            )

        return _failure(tool_request, f"Unsupported tool backend: {definition.backend.value}")

    # Resolve the configured definition for the requested tool.
    def _definition_for_request(self, *, tool_request: ToolRequest) -> ToolDefinition | None:
        definition = self._config.tools.get(tool_request.tool_name)
        if definition is None or not definition.enabled:
            return None
        return definition

    # Execute a real web search using the OpenAI web search tool.
    def _execute_web_search(
        self,
        *,
        tool_request: ToolRequest,
        definition: ToolDefinition,
    ) -> ToolExecutionResult:
        if OpenAI is None:
            return _failure(tool_request, "The 'openai' package is not installed.")
        if not self._settings.api_key:
            return _failure(tool_request, "OPENAI_API_KEY is missing for web_search.")

        if self._client is None:
            self._client = OpenAI(
                api_key=self._settings.api_key,
                timeout=self._settings.timeout_seconds,
            )

        try:
            response = self._client.responses.create(
                model=self._settings.model,
                input=tool_request.query,
                tools=[{"type": "web_search_preview"}],
            )
        except Exception as exc:  # pragma: no cover - network/client errors
            return _failure(tool_request, f"Web search failed: {exc}")

        content = getattr(response, "output_text", None)
        sources = _extract_web_sources(response)

        return ToolExecutionResult(
            request=tool_request,
            success=True,
            summary="Web search executed successfully.",
            content=_truncate_text(content, definition.max_content_chars) if content else None,
            sources=sources,
        )

    # Fetch one URL and extract readable text using only the standard library.
    def _execute_fetch_url_text(
        self,
        *,
        tool_request: ToolRequest,
        definition: ToolDefinition,
    ) -> ToolExecutionResult:
        try:
            request = Request(
                tool_request.url,
                headers={
                    "User-Agent": "Layer2ToolGateway/1.0",
                    "Accept": "text/html, text/plain;q=0.9, */*;q=0.1",
                },
            )
            with urlopen(request, timeout=definition.timeout_seconds) as response:
                raw_body = response.read(definition.max_bytes + 1)
                charset = response.headers.get_content_charset() or "utf-8"
                content_type = response.headers.get_content_type()
        except Exception as exc:  # pragma: no cover - network/io errors
            return _failure(tool_request, f"URL fetch failed: {exc}")

        if len(raw_body) > definition.max_bytes:
            return _failure(tool_request, "URL fetch exceeded the configured byte limit.")

        decoded_body = raw_body.decode(charset, errors="replace")

        if content_type == "text/html":
            title = _extract_title(decoded_body) or tool_request.url
            extracted_text = _extract_visible_text(decoded_body)
        else:
            title = tool_request.url
            extracted_text = decoded_body

        content = _truncate_text(extracted_text, definition.max_content_chars)
        snippet = _truncate_text(content, 500)
        if not _is_meaningful_extracted_text(content):
            return _failure(
                tool_request,
                "URL fetch succeeded but extracted readable text was empty.",
            )

        return ToolExecutionResult(
            request=tool_request,
            success=True,
            summary="URL text fetched successfully.",
            content=content,
            sources=(
                ToolSource(
                    title=title,
                    locator=tool_request.url or "",
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


# Load the gateway registry from the configured JSON file or the built-in defaults.
def load_tool_gateway_config(*, config_path: Path | None = None) -> ToolGatewayConfig:
    resolved_path = config_path or get_default_tool_gateway_config_path()
    if not resolved_path.exists():
        return build_default_tool_gateway_config()

    payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    repo_root = Path(__file__).resolve().parents[2]

    mcp_servers = {
        server_id: _parse_mcp_server_definition(server_id, server_payload, repo_root=repo_root)
        for server_id, server_payload in payload.get("mcp_servers", {}).items()
    }
    tools = {
        ToolName(tool_payload["tool_name"]): _parse_tool_definition(tool_payload)
        for tool_payload in payload.get("tools", [])
    }

    return ToolGatewayConfig(tools=tools, mcp_servers=mcp_servers)


# Return the repository-local tool gateway configuration path.
def get_default_tool_gateway_config_path() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "configs" / "layer2_tool_gateway.json"


# Build a conservative in-code default so tests can construct the gateway without files.
def build_default_tool_gateway_config() -> ToolGatewayConfig:
    return ToolGatewayConfig(
        tools={
            ToolName.WEB_SEARCH: ToolDefinition(
                tool_name=ToolName.WEB_SEARCH,
                backend=ToolBackend.OPENAI,
                capability=ToolCapability.READ,
                risk_level=ToolRiskLevel.MEDIUM,
                enabled=True,
                requires_human_review=False,
                timeout_seconds=30.0,
                required_fields=("query",),
            ),
            ToolName.FETCH_URL_TEXT: ToolDefinition(
                tool_name=ToolName.FETCH_URL_TEXT,
                backend=ToolBackend.HTTP_FETCH,
                capability=ToolCapability.READ,
                risk_level=ToolRiskLevel.MEDIUM,
                enabled=True,
                requires_human_review=False,
                timeout_seconds=20.0,
                required_fields=("url",),
            ),
        }
    )


# Parse one MCP server entry into a typed definition.
def _parse_mcp_server_definition(
    server_id: str,
    payload: dict[str, Any],
    *,
    repo_root: Path,
) -> McpServerDefinition:
    command = str(payload.get("command", ""))
    if command == "{python}":
        command = sys.executable

    args = tuple(
        str(arg).replace("{repo_root}", str(repo_root)).replace("{python}", sys.executable)
        for arg in payload.get("args", [])
    )

    return McpServerDefinition(
        server_id=server_id,
        enabled=bool(payload.get("enabled", True)),
        command=command,
        args=args,
        allowed_tools=tuple(str(item) for item in payload.get("allowed_tools", [])),
        timeout_seconds=float(payload.get("timeout_seconds", 5.0)),
    )


# Parse one tool registry entry into a typed definition.
def _parse_tool_definition(payload: dict[str, Any]) -> ToolDefinition:
    return ToolDefinition(
        tool_name=ToolName(payload["tool_name"]),
        backend=ToolBackend(payload["backend"]),
        capability=ToolCapability(payload.get("capability", ToolCapability.READ.value)),
        risk_level=ToolRiskLevel(payload.get("risk_level", ToolRiskLevel.LOW.value)),
        enabled=bool(payload.get("enabled", True)),
        requires_human_review=bool(payload.get("requires_human_review", False)),
        timeout_seconds=float(payload.get("timeout_seconds", 10.0)),
        required_fields=tuple(str(item) for item in payload.get("required_fields", [])),
        max_content_chars=int(payload.get("max_content_chars", 12000)),
        max_bytes=int(payload.get("max_bytes", 200000)),
        allowed_schemes=tuple(str(item) for item in payload.get("allowed_schemes", ["https"])),
        mcp_server_id=payload.get("mcp_server_id"),
        mcp_tool_name=payload.get("mcp_tool_name"),
    )


# Validate one request against the typed gateway registry before external execution.
def _validate_request(
    *,
    tool_request: ToolRequest,
    definition: ToolDefinition,
    config: ToolGatewayConfig,
) -> str | None:
    if not tool_request.reason.strip():
        return "ToolRequest.reason cannot be empty."

    if definition.capability != ToolCapability.READ:
        return "Only read-only tools are enabled in the current gateway baseline."

    for field_name in definition.required_fields:
        value = getattr(tool_request, field_name, None)
        if value is None:
            return f"{tool_request.tool_name.value} requires '{field_name}'."
        if isinstance(value, str) and not value.strip():
            return f"{tool_request.tool_name.value} requires non-empty '{field_name}'."

    if definition.backend == ToolBackend.HTTP_FETCH:
        return _validate_fetch_url(tool_request=tool_request, definition=definition)

    if definition.backend == ToolBackend.MCP:
        return _validate_mcp_tool(definition=definition, config=config)

    return None


# Validate fetch URLs against basic SSRF and scheme constraints.
def _validate_fetch_url(*, tool_request: ToolRequest, definition: ToolDefinition) -> str | None:
    parsed = urlparse(tool_request.url or "")
    if parsed.scheme not in definition.allowed_schemes or not parsed.netloc:
        return "fetch_url_text requires an allowed URL scheme and a valid host."
    if _is_blocked_host(parsed.hostname):
        return "fetch_url_text blocked a local, private, or otherwise unsafe host."
    return None


# Validate that the configured MCP target is enabled and allowlisted.
def _validate_mcp_tool(*, definition: ToolDefinition, config: ToolGatewayConfig) -> str | None:
    if not definition.mcp_server_id or not definition.mcp_tool_name:
        return "MCP tool is missing server or tool mapping."

    server = config.mcp_servers.get(definition.mcp_server_id)
    if server is None or not server.enabled:
        return "MCP server is not registered or is disabled."
    if definition.mcp_tool_name not in server.allowed_tools:
        return "MCP tool is not allowlisted for the configured server."
    return None


# Execute one configured MCP tool call over stdio JSON-RPC.
def _execute_mcp_tool(
    *,
    tool_request: ToolRequest,
    definition: ToolDefinition,
    config: ToolGatewayConfig,
) -> ToolExecutionResult:
    server = config.mcp_servers[definition.mcp_server_id or ""]
    arguments = _build_mcp_arguments(tool_request=tool_request, definition=definition)

    try:
        client = _McpStdioClient(server=server)
        result = client.call_tool(tool_name=definition.mcp_tool_name or "", arguments=arguments)
    except Exception as exc:
        return _failure(tool_request, f"MCP tool execution failed: {exc}")

    return _normalize_mcp_result(
        tool_request=tool_request,
        definition=definition,
        result=result,
    )


# Build allowlisted MCP arguments from typed ToolRequest fields only.
def _build_mcp_arguments(
    *,
    tool_request: ToolRequest,
    definition: ToolDefinition,
) -> dict[str, object]:
    if definition.tool_name == ToolName.MCP_LOCAL_TEST_ECHO:
        return {"text": tool_request.text or tool_request.query or ""}
    if definition.tool_name == ToolName.MCP_LOCAL_TEST_READ_FIXTURE:
        return {"path": tool_request.path or ""}
    if definition.tool_name == ToolName.MCP_LOCAL_TEST_TEXT_STATS:
        return {
            "text": tool_request.text or "",
            "limit": tool_request.limit if tool_request.limit is not None else 5,
        }
    if definition.tool_name == ToolName.MCP_LOCAL_TEST_CALCULATE_EXPRESSION:
        return {"expression": tool_request.expression or ""}
    return {}


# Convert MCP result content into the local ToolExecutionResult contract.
def _normalize_mcp_result(
    *,
    tool_request: ToolRequest,
    definition: ToolDefinition,
    result: dict[str, Any],
) -> ToolExecutionResult:
    raw_content = result.get("content", [])
    text_chunks: list[str] = []

    if isinstance(raw_content, list):
        for item in raw_content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_chunks.append(str(item.get("text", "")))
    elif isinstance(raw_content, str):
        text_chunks.append(raw_content)

    content = _truncate_text("\n".join(chunk for chunk in text_chunks if chunk), definition.max_content_chars)
    is_error = bool(result.get("isError", False))

    return ToolExecutionResult(
        request=tool_request,
        success=not is_error,
        summary=(
            f"MCP tool '{definition.mcp_tool_name}' executed successfully."
            if not is_error
            else f"MCP tool '{definition.mcp_tool_name}' returned an error."
        ),
        content=content,
        sources=(
            ToolSource(
                title=tool_request.tool_name.value,
                locator=f"mcp://{definition.mcp_server_id}/{definition.mcp_tool_name}",
                snippet=_truncate_text(content, 500),
            ),
        )
        if content
        else (),
        error_message=content if is_error else None,
    )


class _McpStdioClient:
    # This minimal client supports the MCP stdio request path needed by tests.
    def __init__(self, *, server: McpServerDefinition) -> None:
        self._server = server

    # Call one MCP tool by starting the configured local server process.
    def call_tool(self, *, tool_name: str, arguments: dict[str, object]) -> dict[str, Any]:
        process = subprocess.Popen(
            [self._server.command, *self._server.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        try:
            self._send_request(process, 1, "initialize", {"protocolVersion": "2024-11-05"})
            self._read_response(process, 1)
            self._send_request(
                process,
                2,
                "tools/call",
                {"name": tool_name, "arguments": arguments},
            )
            response = self._read_response(process, 2)
            if "error" in response:
                raise ToolExecutionError(str(response["error"]))
            result = response.get("result")
            if not isinstance(result, dict):
                raise ToolExecutionError("MCP server returned a malformed result.")
            return result
        finally:
            _terminate_process(process)

    # Send one framed JSON-RPC request to the MCP server.
    def _send_request(
        self,
        process: subprocess.Popen,
        request_id: int,
        method: str,
        params: dict[str, object],
    ) -> None:
        if process.stdin is None:
            raise ToolExecutionError("MCP process stdin is unavailable.")
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        raw_payload = json.dumps(payload).encode("utf-8")
        frame = f"Content-Length: {len(raw_payload)}\r\n\r\n".encode("ascii") + raw_payload
        process.stdin.write(frame)
        process.stdin.flush()

    # Read one framed JSON-RPC response from the MCP server with a timeout.
    def _read_response(self, process: subprocess.Popen, request_id: int) -> dict[str, Any]:
        if process.stdout is None:
            raise ToolExecutionError("MCP process stdout is unavailable.")

        deadline = time.monotonic() + self._server.timeout_seconds
        header_bytes = b""
        while b"\r\n\r\n" not in header_bytes:
            if time.monotonic() > deadline:
                raise ToolExecutionError("MCP response timed out.")
            next_byte = process.stdout.read(1)
            if not next_byte:
                raise ToolExecutionError("MCP process closed before sending a response.")
            header_bytes += next_byte

        headers, remaining = header_bytes.split(b"\r\n\r\n", 1)
        content_length = _parse_content_length(headers)
        body = remaining
        while len(body) < content_length:
            if time.monotonic() > deadline:
                raise ToolExecutionError("MCP response body timed out.")
            chunk = process.stdout.read(content_length - len(body))
            if not chunk:
                raise ToolExecutionError("MCP process closed during response body.")
            body += chunk

        response = json.loads(body.decode("utf-8"))
        if response.get("id") != request_id:
            raise ToolExecutionError("MCP server returned an unexpected response id.")
        return response


# Parse the Content-Length header used by MCP stdio framing.
def _parse_content_length(headers: bytes) -> int:
    for raw_line in headers.split(b"\r\n"):
        line = raw_line.decode("ascii", errors="replace")
        if line.lower().startswith("content-length:"):
            return int(line.split(":", 1)[1].strip())
    raise ToolExecutionError("MCP response omitted Content-Length.")


# Terminate a local MCP process without leaving it running after one call.
def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)

    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            stream.close()


# Return one structured failure result for invalid or failed tool execution.
def _failure(tool_request: ToolRequest, message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        request=tool_request,
        success=False,
        summary=message,
        error_message=message,
    )


# Detect hosts that should not be fetched by the read-only URL tool.
def _is_blocked_host(hostname: str | None) -> bool:
    if hostname is None:
        return True

    normalized = hostname.strip().lower().rstrip(".")
    if normalized in {"localhost"} or normalized.endswith(".localhost"):
        return True

    try:
        addresses = [ipaddress.ip_address(normalized)]
    except ValueError:
        try:
            addresses = [
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
            ]
        except socket.gaierror:
            return False

    return any(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        for address in addresses
    )


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


# Treat tiny title-only pages as empty extraction for evidence purposes.
def _is_meaningful_extracted_text(text: str | None) -> bool:
    if text is None:
        return False
    return len(" ".join(text.split())) >= 40


# Keep tool content bounded so TaskState stays manageable.
def _truncate_text(text: str | None, limit: int) -> str | None:
    if text is None:
        return None

    if len(text) <= limit:
        return text

    return text[: limit - 3].rstrip() + "..."


# Load simple KEY=VALUE pairs from the supported .env file locations.
def _load_env_file_values() -> dict[str, str]:
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


# Search both repository root and src-level env files for local development.
def _candidate_env_paths() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[2]
    return [
        repo_root / ".env",
        repo_root / "src" / ".env",
    ]


# Build the production tool gateway used by the live Layer 2 runtime.
def build_default_tool_gateway() -> ToolGatewayPort:
    return OpenAIToolGateway()


__all__ = [
    "McpServerDefinition",
    "OpenAIToolGateway",
    "OpenAIToolGatewaySettings",
    "ToolBackend",
    "ToolCapability",
    "ToolDefinition",
    "ToolExecutionError",
    "ToolGatewayConfig",
    "ToolRiskLevel",
    "build_default_tool_gateway",
    "build_default_tool_gateway_config",
    "get_default_tool_gateway_config_path",
    "load_tool_gateway_config",
]
