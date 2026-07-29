from __future__ import annotations

from ...actor_contract import ActorPort, ActorResult, ActorTask
from .bedrock_runtime import BedrockChatClient, BedrockRuntimeError, BedrockRuntimeSettings
from ..action_schemas import (
    build_actor_runtime_tools,
    select_actor_runtime_tools_for_turn,
)
from ..errors import ActorExecutionError
from ..prompts import build_actor_system_prompt, build_actor_user_prompt
from ..response_parser import convert_json_payload_to_tool_call
from ...tools import ToolGatewayConfig
from ..result_builder import (
    build_provider_blocked_result,
    build_provider_protocol_failure_result,
    build_provider_replan_result,
    convert_tool_call_to_actor_result, convert_tool_calls_to_actor_result,
)
from ..settings import BedrockNovaActorSettings


class BedrockNovaActor(ActorPort):
    # Execute the actor role through the configured Bedrock Converse model; the public class name is retained.
    actor_name = "bedrock_nova_2_lite_actor"
    prompt_version = "actor_v9_bedrock_converse_tool_calling"

    def __init__(
        self,
        settings: BedrockNovaActorSettings | None = None,
        *,
        tool_gateway_config: ToolGatewayConfig | None = None,
    ) -> None:
        self._settings = settings or BedrockNovaActorSettings.from_env()
        self._runtime_tools = build_actor_runtime_tools(tool_gateway_config)
        self._client = BedrockChatClient(
            BedrockRuntimeSettings(
                model_id=self._settings.model_id,
                region_name=self._settings.region_name,
                profile_name=self._settings.profile_name,
                access_key_id=self._settings.access_key_id,
                secret_access_key=self._settings.secret_access_key,
                session_token=self._settings.session_token,
                read_timeout_seconds=self._settings.read_timeout_seconds,
                connect_timeout_seconds=self._settings.connect_timeout_seconds,
            )
        )

    # Ask Bedrock for one action and normalize it into ActorResult.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        runtime_tools = select_actor_runtime_tools_for_turn(
            self._runtime_tools, task_spec=actor_task.task_spec, actor_task=actor_task
        )
        allowed_action_names = {str(tool["name"]) for tool in runtime_tools}
        try:
            if hasattr(self._client, "complete_tool_calls"):
                payloads = self._client.complete_tool_calls(
                    system_prompt=build_actor_system_prompt(),
                    user_prompt=build_actor_user_prompt(actor_task=actor_task),
                    tools=runtime_tools,
                )
            else:
                # Keep test and third-party compatibility clients on the former one-call method.
                payloads = (self._client.complete_tool_call(
                    system_prompt=build_actor_system_prompt(),
                    user_prompt=build_actor_user_prompt(actor_task=actor_task),
                    tools=runtime_tools,
                ),)
        except BedrockRuntimeError as exc:
            if "no tool use" in str(exc):
                return build_provider_blocked_result(actor_task=actor_task, detail=str(exc))
            raise ActorExecutionError(f"Bedrock actor request failed: {exc}") from exc
        try:
            return convert_tool_calls_to_actor_result(
                actor_task=actor_task,
                tool_calls=tuple(
                    convert_json_payload_to_tool_call(payload, allowed_action_names=allowed_action_names)
                    for payload in payloads
                ),
            )
        except ActorExecutionError as exc:
            return build_provider_protocol_failure_result(actor_task=actor_task, detail=str(exc), error=exc)


__all__ = ["BedrockNovaActor"]
