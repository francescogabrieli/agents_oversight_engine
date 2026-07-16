from __future__ import annotations

import json
import subprocess
import time
from typing import Any

from ..contracts import ToolExecutionResult, ToolName, ToolRequest, ToolSource
from ..models import McpServerDefinition, ToolDefinition, ToolExecutionError, ToolGatewayConfig
from ..results import failure_result
from ..text import truncate_text


class McpToolAdapter:
    # Own configured stdio MCP execution without exposing MCP to the actor.
    def __init__(self, *, config: ToolGatewayConfig) -> None:
        self._config = config

    # Execute one allowlisted MCP request and normalize its result.
    def execute(
        self,
        *,
        tool_request: ToolRequest,
        definition: ToolDefinition,
    ) -> ToolExecutionResult:
        server = self._config.mcp_servers[definition.mcp_server_id or ""]
        arguments = build_mcp_arguments(tool_request=tool_request, definition=definition)
        try:
            result = _McpStdioClient(server=server).call_tool(
                tool_name=definition.mcp_tool_name or "",
                arguments=arguments,
            )
        except Exception as exc:
            return failure_result(tool_request, f"MCP tool execution failed: {exc}")
        return normalize_mcp_result(
            tool_request=tool_request,
            definition=definition,
            result=result,
        )


# Build allowlisted MCP arguments from typed ToolRequest fields only.
def build_mcp_arguments(
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


# Convert MCP content into the local ToolExecutionResult contract.
def normalize_mcp_result(
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
    content = truncate_text(
        "\n".join(chunk for chunk in text_chunks if chunk),
        definition.max_content_chars,
    )
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
                snippet=truncate_text(content, 500),
            ),
        )
        if content
        else (),
        error_message=content if is_error else None,
    )


class _McpStdioClient:
    # This bounded client supports the configured MCP stdio request path.
    def __init__(self, *, server: McpServerDefinition) -> None:
        self._server = server

    # Call one MCP tool through a short-lived configured local process.
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
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        raw_payload = json.dumps(payload).encode("utf-8")
        frame = f"Content-Length: {len(raw_payload)}\r\n\r\n".encode("ascii") + raw_payload
        process.stdin.write(frame)
        process.stdin.flush()

    # Read one framed JSON-RPC response with a configured timeout.
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


__all__ = ["McpToolAdapter", "build_mcp_arguments", "normalize_mcp_result"]
