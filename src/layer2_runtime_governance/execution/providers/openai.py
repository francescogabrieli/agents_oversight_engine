from __future__ import annotations

from llm_retry import llm_retry

from ...actor_contract import ActorPort, ActorResult, ActorTask
from ..action_schemas import (
    build_actor_runtime_tools,
    select_actor_runtime_tools_for_turn,
)
from ..errors import ActorExecutionError
from ..prompts import build_actor_system_prompt, build_actor_user_prompt
from ..response_parser import extract_function_calls, validate_tool_call_action
from layer3_tool_bus_external_capabilities.tools import ToolGatewayConfig
from ..result_builder import (
    build_provider_protocol_failure_result,
    build_provider_replan_result,
    convert_tool_calls_to_actor_result,
)
from ..settings import OpenAIActorSettings

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - dependency is checked during construction.
    OpenAI = None


class OpenAIActor(ActorPort):
    # Execute one bounded actor turn through OpenAI native function calling.
    actor_name = "openai_layer2_actor"
    prompt_version = "actor_v7_runtime_tool_binding"

    def __init__(
        self,
        settings: OpenAIActorSettings | None = None,
        *,
        tool_gateway_config: ToolGatewayConfig | None = None,
    ) -> None:
        self._settings = settings or OpenAIActorSettings.from_env()
        self._runtime_tools = build_actor_runtime_tools(tool_gateway_config)
        if OpenAI is None:
            raise RuntimeError(
                "The 'openai' package is not installed. Install it before using the Layer 2 actor."
            )
        if not self._settings.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is missing. Set it in the process environment or in .env."
            )
        self._client = OpenAI(
            api_key=self._settings.api_key,
            timeout=self._settings.timeout_seconds,
        )

    # Ask the provider for one action and normalize it into ActorResult.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        runtime_tools = select_actor_runtime_tools_for_turn(
            self._runtime_tools, task_spec=actor_task.task_spec, actor_task=actor_task
        )
        allowed_action_names = {str(tool["name"]) for tool in runtime_tools}
        try:
            response = self._request_actor_response(
                actor_task=actor_task,
                runtime_tools=runtime_tools,
            )
        except Exception as exc:  # pragma: no cover - provider errors are normalized.
            raise ActorExecutionError(f"Actor request failed: {exc}") from exc
        try:
            return convert_tool_calls_to_actor_result(
                actor_task=actor_task,
                tool_calls=tuple(
                    validate_tool_call_action(
                        tool_call=tool_call,
                        allowed_action_names=allowed_action_names,
                    )
                    for tool_call in extract_function_calls(response)
                ),
            )
        except ActorExecutionError as exc:
            return build_provider_protocol_failure_result(actor_task=actor_task, detail=str(exc), error=exc)

    # Retry only transient provider failures for one bounded action request.
    @llm_retry
    def _request_actor_response(
        self,
        *,
        actor_task: ActorTask,
        runtime_tools: list[dict[str, object]],
    ) -> object:
        return self._client.responses.create(
            model=self._settings.model,
            input=[
                {"role": "system", "content": build_actor_system_prompt()},
                {"role": "user", "content": build_actor_user_prompt(actor_task=actor_task)},
            ],
            tools=runtime_tools,
            tool_choice="required",
            parallel_tool_calls=False,
        )


__all__ = ["OpenAIActor"]
