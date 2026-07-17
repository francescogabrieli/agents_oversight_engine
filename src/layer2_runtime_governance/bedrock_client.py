"""Compatibility facade for the Bedrock execution transport."""

from .execution.providers.bedrock_runtime import (
    BedrockChatClient,
    BedrockRuntimeError,
    BedrockRuntimeSettings,
)

__all__ = ["BedrockChatClient", "BedrockRuntimeError", "BedrockRuntimeSettings"]
