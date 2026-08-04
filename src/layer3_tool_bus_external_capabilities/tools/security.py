from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from .contracts import ToolRequest
from .models import ToolBackend, ToolCapability, ToolDefinition, ToolGatewayConfig


# Validate one request against the registry before external execution.
def validate_tool_request(
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
        return validate_fetch_url(tool_request=tool_request, definition=definition)
    if definition.backend == ToolBackend.MCP:
        return validate_mcp_target(definition=definition, config=config)
    return None


# Validate fetch URLs against scheme and server-side request forgery constraints.
def validate_fetch_url(*, tool_request: ToolRequest, definition: ToolDefinition) -> str | None:
    parsed = urlparse(tool_request.url or "")
    if parsed.scheme not in definition.allowed_schemes or not parsed.netloc:
        return "fetch_url_text requires an allowed URL scheme and a valid host."
    if is_blocked_host(parsed.hostname):
        return "fetch_url_text blocked a local, private, or otherwise unsafe host."
    return None


# Validate that the configured MCP server and tool are enabled and allowlisted.
def validate_mcp_target(*, definition: ToolDefinition, config: ToolGatewayConfig) -> str | None:
    if not definition.mcp_server_id or not definition.mcp_tool_name:
        return "MCP tool is missing server or tool mapping."
    server = config.mcp_servers.get(definition.mcp_server_id)
    if server is None or not server.enabled:
        return "MCP server is not registered or is disabled."
    if definition.mcp_tool_name not in server.allowed_tools:
        return "MCP tool is not allowlisted for the configured server."
    return None


# Detect local, private, reserved, or otherwise unsafe fetch targets.
def is_blocked_host(hostname: str | None) -> bool:
    if hostname is None:
        return True
    normalized = hostname.strip().lower().rstrip(".")
    if normalized == "localhost" or normalized.endswith(".localhost"):
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


__all__ = [
    "is_blocked_host",
    "validate_fetch_url",
    "validate_mcp_target",
    "validate_tool_request",
]
