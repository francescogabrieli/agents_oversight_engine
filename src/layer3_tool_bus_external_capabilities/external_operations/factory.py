from __future__ import annotations

from .contracts import ExternalOperationExecutorPort
from .execution import DeferredExternalOperationExecutor
from .registry import ExternalCapabilityRegistry


# Select the real MCP adapter whenever configuration enables an executable capability.
def build_default_external_operation_executor(
    *,
    registry: ExternalCapabilityRegistry,
) -> ExternalOperationExecutorPort:
    if any(definition.enabled for definition in registry.capabilities.values()):
        # Keep the SDK import at the infrastructure composition boundary.
        from .mcp import MCPExternalOperationExecutor

        return MCPExternalOperationExecutor(registry=registry)
    return DeferredExternalOperationExecutor()


__all__ = ["build_default_external_operation_executor"]
