"""Compatibility facade for the modular governed tool capability.

New code should import from ``layer2_runtime_governance.tools``. This module
keeps the previous public import path stable while callers migrate.
"""

from .tools import (
    McpServerDefinition,
    OpenAIToolGateway,
    OpenAIToolGatewaySettings,
    ToolBackend,
    ToolCapability,
    ToolDefinition,
    ToolExecutionError,
    ToolGatewayConfig,
    ToolRiskLevel,
    build_default_tool_gateway,
    build_default_tool_gateway_config,
    get_default_tool_gateway_config_path,
    load_tool_gateway_config,
)

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
