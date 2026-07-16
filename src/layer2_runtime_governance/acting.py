"""Compatibility facade for modular Layer 2 actor execution.

New code should import provider-independent contracts from ``actor_contract``
and concrete execution components from ``execution``.
"""

from .actor_contract import ActorPort
from .execution.errors import ActorExecutionError
from .execution.prompts import build_actor_system_prompt, build_actor_user_prompt
from .execution.providers import BedrockNovaActor, OpenAIActor
from .execution.settings import (
    BedrockNovaActorSettings,
    OpenAIActorSettings,
    resolve_actor_provider,
)


# Build the configured production actor without coupling planner or replanner providers.
def build_default_actor() -> ActorPort:
    provider = resolve_actor_provider()
    if provider == "openai":
        return OpenAIActor()
    if provider == "bedrock":
        return BedrockNovaActor()
    raise RuntimeError("Unsupported LAYER2_ACTOR_PROVIDER. Use 'bedrock' or 'openai'.")


__all__ = [
    "ActorExecutionError",
    "BedrockNovaActor",
    "BedrockNovaActorSettings",
    "OpenAIActor",
    "OpenAIActorSettings",
    "build_actor_system_prompt",
    "build_actor_user_prompt",
    "build_default_actor",
]
