from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .contracts import ToolName


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


# ToolRiskLevel records a coarse governance label consumed by policy signals.
class ToolRiskLevel(str, Enum):
    NONE = "none"
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
    # Provider and registry settings stay separate from gateway orchestration.
    model: str = "gpt-5.4-nano"
    api_key: str | None = None
    timeout_seconds: float = 30.0
    fetch_timeout_seconds: float = 20.0
    config_path: Path | None = None

    @classmethod
    def from_env(cls) -> "OpenAIToolGatewaySettings":
        # Merge process env and local .env files into one settings object.
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
    """Raised when a tool adapter cannot execute a valid request."""


# Load simple KEY=VALUE pairs from supported local development files.
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


# Search repository root and src-level env files for local development.
def _candidate_env_paths() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[3]
    return [repo_root / ".env", repo_root / "src" / ".env"]


__all__ = [
    "McpServerDefinition",
    "OpenAIToolGatewaySettings",
    "ToolBackend",
    "ToolCapability",
    "ToolDefinition",
    "ToolExecutionError",
    "ToolGatewayConfig",
    "ToolRiskLevel",
]
