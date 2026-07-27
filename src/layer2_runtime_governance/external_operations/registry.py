from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from .contracts import (
    ExternalOperationEffect,
    ExternalOperationFamily,
    GenericPayloadField,
    OperationBoundary,
)
from ..infrastructure.environment import get_env_value


class ExternalConnectorKind(str, Enum):
    # The external executor currently admits only registered MCP connectors.
    MCP = "mcp"


class MCPTransportKind(str, Enum):
    # Stdio reuses the repository transport while the pool owns the SDK lifecycle.
    STDIO = "stdio"


class IdempotencySupport(str, Enum):
    # Capability metadata states what the backend can actually guarantee.
    NONE = "none"
    CLIENT_KEY = "client_key"
    BACKEND_KEY = "backend_key"


@dataclass(frozen=True)
class ExternalMCPServerDefinition:
    # Server commands are configuration-owned and never come from actor text.
    server_id: str
    enabled: bool
    transport: MCPTransportKind
    command: str
    args: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    timeout_seconds: float
    environment_keys: tuple[str, ...] = ()
    working_directory: str | None = None


@dataclass(frozen=True)
class ExternalCapabilityDefinition:
    # One registry entry binds a canonical capability to one exact MCP tool.
    capability_id: str
    operation_family: ExternalOperationFamily
    normalized_effect: ExternalOperationEffect
    operation_boundary: OperationBoundary
    enabled: bool
    connector_kind: ExternalConnectorKind
    mcp_server_id: str
    mcp_tool_name: str
    allowed_normalized_effects: tuple[ExternalOperationEffect, ...]
    input_schema_version: str
    input_schema: dict[str, Any]
    output_schema_version: str
    consequence_profile: str
    timeout_seconds: float
    idempotency_support: IdempotencySupport
    max_attempts: int = 1
    defensive_constraints: tuple[GenericPayloadField, ...] = ()


@dataclass(frozen=True)
class ExternalCapabilityRegistry:
    # The immutable registry is shared by validation and the MCP adapter.
    registry_version: str
    capabilities: dict[str, ExternalCapabilityDefinition]
    mcp_servers: dict[str, ExternalMCPServerDefinition] = field(default_factory=dict)

    # Resolve only enabled, completely mapped capability definitions.
    def resolve(self, capability_id: str) -> ExternalCapabilityDefinition | None:
        definition = self.capabilities.get(capability_id)
        if definition is None or not definition.enabled:
            return None
        return definition


# Load the dedicated external registry without exposing it to actor tool binding.
def load_external_capability_registry(
    *,
    config_path: Path | None = None,
) -> ExternalCapabilityRegistry:
    configured = config_path or _configured_registry_path()
    if not configured.exists():
        return ExternalCapabilityRegistry(registry_version="external_capabilities.v1", capabilities={})
    return _load_external_capability_registry_cached(configured.resolve())


# Parse and cache one immutable external-capability registry.
@lru_cache(maxsize=8)
def _load_external_capability_registry_cached(
    resolved_path: Path,
) -> ExternalCapabilityRegistry:
    payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    repo_root = Path(__file__).resolve().parents[3]
    servers = {
        str(server_id): _parse_server(
            server_id=str(server_id),
            payload=server_payload,
            repo_root=repo_root,
        )
        for server_id, server_payload in payload.get("mcp_servers", {}).items()
    }
    capabilities = {
        definition.capability_id: definition
        for definition in (
            _parse_capability(item)
            for item in payload.get("capabilities", [])
        )
    }
    registry = ExternalCapabilityRegistry(
        registry_version=str(payload.get("registry_version", "external_capabilities.v1")),
        capabilities=capabilities,
        mcp_servers=servers,
    )
    _validate_registry(registry)
    return registry


# Parse a configured stdio server while resolving repository-safe placeholders.
def _parse_server(
    *,
    server_id: str,
    payload: dict[str, Any],
    repo_root: Path,
) -> ExternalMCPServerDefinition:
    command = str(payload.get("command", ""))
    if command == "{python}":
        command = sys.executable
    args = tuple(
        str(item)
        .replace("{repo_root}", str(repo_root))
        .replace("{python}", sys.executable)
        for item in payload.get("args", [])
    )
    return ExternalMCPServerDefinition(
        server_id=server_id,
        enabled=bool(payload.get("enabled", False)),
        transport=MCPTransportKind(payload.get("transport", MCPTransportKind.STDIO.value)),
        command=command,
        args=args,
        allowed_tools=tuple(str(item) for item in payload.get("allowed_tools", [])),
        timeout_seconds=float(payload.get("timeout_seconds", 15.0)),
        environment_keys=tuple(str(item) for item in payload.get("environment_keys", [])),
        working_directory=(
            str(payload.get("working_directory", "{repo_root}\\src")).replace(
                "{repo_root}", str(repo_root)
            )
        ),
    )


# Parse one capability entry without capability-specific branching.
def _parse_capability(payload: dict[str, Any]) -> ExternalCapabilityDefinition:
    return ExternalCapabilityDefinition(
        capability_id=str(payload["capability_id"]),
        operation_family=ExternalOperationFamily(payload["operation_family"]),
        normalized_effect=ExternalOperationEffect(payload["normalized_effect"]),
        operation_boundary=OperationBoundary(payload["operation_boundary"]),
        enabled=bool(payload.get("enabled", False)),
        connector_kind=ExternalConnectorKind(payload.get("connector_kind", "mcp")),
        mcp_server_id=str(payload["mcp_server_id"]),
        mcp_tool_name=str(payload["mcp_tool_name"]),
        allowed_normalized_effects=tuple(
            ExternalOperationEffect(item)
            for item in payload.get(
                "allowed_normalized_effects",
                [payload["normalized_effect"]],
            )
        ),
        input_schema_version=str(payload["input_schema_version"]),
        input_schema=dict(payload.get("input_schema", {})),
        output_schema_version=str(payload["output_schema_version"]),
        consequence_profile=str(payload["consequence_profile"]),
        timeout_seconds=float(payload.get("timeout_seconds", 15.0)),
        idempotency_support=IdempotencySupport(
            payload.get("idempotency_support", IdempotencySupport.NONE.value)
        ),
        max_attempts=max(1, min(int(payload.get("max_attempts", 1)), 2)),
        defensive_constraints=tuple(
            GenericPayloadField(name=str(key), value=str(value))
            for key, value in payload.get("defensive_constraints", {}).items()
        ),
    )


# Fail closed when enabled capability mappings are incomplete or inconsistent.
def _validate_registry(registry: ExternalCapabilityRegistry) -> None:
    for capability in registry.capabilities.values():
        if capability.connector_kind != ExternalConnectorKind.MCP:
            raise ValueError("External capability registry permits only MCP connectors.")
        server = registry.mcp_servers.get(capability.mcp_server_id)
        if not capability.enabled:
            continue
        if server is None or not server.enabled:
            raise ValueError(
                f"Enabled capability '{capability.capability_id}' has no enabled MCP server."
            )
        if capability.mcp_tool_name not in server.allowed_tools:
            raise ValueError(
                f"Capability '{capability.capability_id}' maps to a non-allowlisted MCP tool."
            )
        if capability.operation_boundary != OperationBoundary.GOVERNED_EXTERNAL:
            raise ValueError("External execution capabilities must use governed_external boundary.")
        if not capability.allowed_normalized_effects:
            raise ValueError("Enabled capabilities require at least one normalized effect.")
        if capability.idempotency_support == IdempotencySupport.NONE:
            raise ValueError("Enabled mutating capabilities require idempotency support.")


# Select the configured registry path without reading any secret values.
def _configured_registry_path() -> Path:
    configured = get_env_value("LAYER2_EXTERNAL_CAPABILITY_CONFIG_PATH")
    if configured:
        return Path(configured)
    return (
        Path(__file__).resolve().parents[3]
        / "configs"
        / "layer2_external_capabilities.json"
    )


__all__ = [
    "ExternalCapabilityDefinition",
    "ExternalCapabilityRegistry",
    "ExternalConnectorKind",
    "ExternalMCPServerDefinition",
    "IdempotencySupport",
    "MCPTransportKind",
    "load_external_capability_registry",
]
