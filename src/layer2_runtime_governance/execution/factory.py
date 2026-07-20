"""Composition helpers for the configured Layer 2 actor."""

from ..actor_contract import ActorPort
from .providers import BedrockNovaActor, OpenAIActor
from .settings import resolve_actor_provider


# Build the configured production actor without coupling graph wiring to providers.
def build_default_actor() -> ActorPort:
    provider = resolve_actor_provider()
    if provider == "openai":
        return OpenAIActor()
    if provider == "bedrock":
        return BedrockNovaActor()
    raise RuntimeError("Unsupported LAYER2_ACTOR_PROVIDER. Use 'bedrock' or 'openai'.")


__all__ = ["build_default_actor"]
