from __future__ import annotations

from .contracts import ToolExecutionResult, ToolRequest


# Return one structured failure for invalid or unsuccessful tool execution.
def failure_result(tool_request: ToolRequest, message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        request=tool_request,
        success=False,
        summary=message,
        error_message=message,
    )


__all__ = ["failure_result"]
