from __future__ import annotations

from .adapters import (
    ControlledCorpusAdapter,
    HttpFetchAdapter,
    McpToolAdapter,
    OpenAIWebSearchAdapter,
)
from .contracts import ToolExecutionResult, ToolGatewayPort, ToolRequest
from .models import OpenAIToolGatewaySettings, ToolBackend, ToolDefinition, ToolGatewayConfig
from .registry import load_tool_gateway_config
from .results import failure_result
from .security import validate_tool_request


class OpenAIToolGateway(ToolGatewayPort):
    # Preserve the public name while the gateway now dispatches to cohesive adapters.
    gateway_name = "openai_tool_gateway"

    def __init__(
        self,
        settings: OpenAIToolGatewaySettings | None = None,
        config: ToolGatewayConfig | None = None,
        *,
        web_search_adapter: OpenAIWebSearchAdapter | None = None,
        http_fetch_adapter: HttpFetchAdapter | None = None,
        mcp_adapter: McpToolAdapter | None = None,
        controlled_corpus_adapter: ControlledCorpusAdapter | None = None,
    ) -> None:
        # Resolve dependencies once so each worker owns an explicit gateway instance.
        self._settings = settings or OpenAIToolGatewaySettings.from_env()
        self._config = config or load_tool_gateway_config(config_path=self._settings.config_path)
        self._web_search_adapter = web_search_adapter or OpenAIWebSearchAdapter(
            settings=self._settings
        )
        self._http_fetch_adapter = http_fetch_adapter or HttpFetchAdapter()
        self._mcp_adapter = mcp_adapter or McpToolAdapter(config=self._config)
        self._controlled_corpus_adapter = (
            controlled_corpus_adapter
            or ControlledCorpusAdapter(
                config_path=self._settings.controlled_corpus_config_path
            )
        )

    # Validate and dispatch one typed request to its configured backend adapter.
    def execute(self, *, tool_request: ToolRequest) -> ToolExecutionResult:
        definition = self._definition_for_request(tool_request=tool_request)
        if definition is None:
            return failure_result(tool_request, "Tool is not registered or is disabled.")
        validation_error = validate_tool_request(
            tool_request=tool_request,
            definition=definition,
            config=self._config,
        )
        if validation_error is not None:
            return failure_result(tool_request, validation_error)
        if definition.backend == ToolBackend.OPENAI:
            return self._web_search_adapter.execute(
                tool_request=tool_request,
                definition=definition,
            )
        if definition.backend == ToolBackend.HTTP_FETCH:
            return self._http_fetch_adapter.execute(
                tool_request=tool_request,
                definition=definition,
            )
        if definition.backend == ToolBackend.MCP:
            return self._mcp_adapter.execute(
                tool_request=tool_request,
                definition=definition,
            )
        if definition.backend == ToolBackend.LOCAL_RUNTIME:
            return self._controlled_corpus_adapter.execute(
                tool_request=tool_request,
                definition=definition,
            )
        return failure_result(
            tool_request,
            f"Unsupported tool backend: {definition.backend.value}",
        )

    # Resolve an enabled registry definition for the requested tool.
    def _definition_for_request(self, *, tool_request: ToolRequest) -> ToolDefinition | None:
        definition = self._config.tools.get(tool_request.tool_name)
        if definition is None or not definition.enabled:
            return None
        return definition


# Build the production gateway used by the live Layer 2 runtime.
def build_default_tool_gateway(
    *,
    config: ToolGatewayConfig | None = None,
) -> ToolGatewayPort:
    return OpenAIToolGateway(config=config)


__all__ = ["OpenAIToolGateway", "build_default_tool_gateway"]
