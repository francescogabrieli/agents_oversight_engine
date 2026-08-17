from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from .contracts import ToolName
from .models import (
    McpServerDefinition,
    ToolBackend,
    ToolCapability,
    ToolDefinition,
    ToolGatewayConfig,
    ToolRiskLevel,
)
from ..external_operations.contracts import (
    ExternalOperationEffect,
    ExternalOperationFamily,
    OperationBoundary,
)


# Load the gateway registry from its explicit JSON contract.
def load_tool_gateway_config(*, config_path: Path | None = None) -> ToolGatewayConfig:
    resolved_path = config_path or get_default_tool_gateway_config_path()
    if not resolved_path.exists():
        raise FileNotFoundError(f"Tool Gateway configuration does not exist: {resolved_path}")
    return _load_tool_gateway_config_cached(resolved_path.resolve())


# Cache the immutable registry so governance does not reread JSON on every actor turn.
@lru_cache(maxsize=8)
def _load_tool_gateway_config_cached(resolved_path: Path) -> ToolGatewayConfig:
    payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    repo_root = Path(__file__).resolve().parents[3]
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
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "configs" / "layer2_tool_gateway.json"


# Load the repository default without maintaining a second code-defined registry.
def build_default_tool_gateway_config() -> ToolGatewayConfig:
    return load_tool_gateway_config()


# Parse one configured MCP server into a typed definition.
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
        # Unknown configured capabilities cross the governed boundary unless explicitly classified.
        operation_boundary=OperationBoundary(
            payload.get("operation_boundary", OperationBoundary.GOVERNED_EXTERNAL.value)
        ),
        operation_family=ExternalOperationFamily(
            payload.get("operation_family", ExternalOperationFamily.GENERIC.value)
        ),
        normalized_effect=ExternalOperationEffect(
            payload.get("normalized_effect", ExternalOperationEffect.UNKNOWN.value)
        ),
        capability_profile_id=payload.get("capability_profile_id"),
        capability_profile_version=payload.get("capability_profile_version"),
    )


__all__ = [
    "build_default_tool_gateway_config",
    "get_default_tool_gateway_config_path",
    "load_tool_gateway_config",
]
