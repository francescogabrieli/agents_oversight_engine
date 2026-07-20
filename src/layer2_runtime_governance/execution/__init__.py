"""Actor execution settings and cohesive execution support modules."""

from .errors import ActorExecutionError
from .factory import build_default_actor
from .action_schemas import build_actor_runtime_tools, build_json_actor_output_shape
from .prompts import build_actor_system_prompt, build_actor_user_prompt
from .prompt_projection import ACTOR_SCRATCHPAD_ENTRY_LIMIT, build_actor_prompt_payload
from .response_parser import (
    coerce_provider_confidence,
    convert_json_payload_to_tool_call,
    extract_single_function_call,
    normalize_actor_payload,
    validate_tool_call_action,
)
from .settings import BedrockNovaActorSettings, OpenAIActorSettings, resolve_actor_provider
from .providers import BedrockNovaActor, OpenAIActor

__all__ = [
    "ActorExecutionError",
    "BedrockNovaActor",
    "BedrockNovaActorSettings",
    "OpenAIActorSettings",
    "OpenAIActor",
    "build_default_actor",
    "build_actor_system_prompt",
    "build_actor_user_prompt",
    "ACTOR_SCRATCHPAD_ENTRY_LIMIT",
    "build_actor_prompt_payload",
    "build_actor_runtime_tools",
    "build_json_actor_output_shape",
    "coerce_provider_confidence",
    "convert_json_payload_to_tool_call",
    "extract_single_function_call",
    "normalize_actor_payload",
    "validate_tool_call_action",
    "resolve_actor_provider",
]
