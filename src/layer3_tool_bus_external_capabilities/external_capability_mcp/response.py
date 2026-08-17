from datetime import datetime
from typing import Any


# Build the closed response envelope shared by productive MCP connectors.
def build_tool_response(
    *,
    provider: str,
    status: str,
    summary: str,
    error_code: str | None,
    retryable: bool,
    timestamp: datetime,
    external_reference: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "external_execution.tool_response.v1",
        "status": status,
        "external_reference": external_reference,
        "provider": provider,
        "timestamp": timestamp.isoformat(),
        "summary": summary,
        "error_code": error_code,
        "retryable": retryable,
        "metadata": metadata or {},
    }


__all__ = ["build_tool_response"]
