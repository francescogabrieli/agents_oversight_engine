"""Composition helpers for the configured Layer 2 actor."""

from ..actor_contract import ActorPort
from .providers import BedrockNovaActor, OpenAIActor
from .settings import resolve_actor_provider
from ..tools import ToolGatewayConfig


# Build the configured production actor without coupling graph wiring to providers.
def build_default_actor(*, tool_gateway_config: ToolGatewayConfig | None = None) -> ActorPort:
    provider = resolve_actor_provider()
    if provider == "openai":
        return OpenAIActor(tool_gateway_config=tool_gateway_config)
    if provider == "bedrock":
        return BedrockNovaActor(tool_gateway_config=tool_gateway_config)
    raise RuntimeError("Unsupported LAYER2_ACTOR_PROVIDER. Use 'bedrock' or 'openai'.")


__all__ = ["build_default_actor"]
