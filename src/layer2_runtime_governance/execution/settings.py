from __future__ import annotations

import os
from dataclasses import dataclass

from .providers.bedrock_runtime import BedrockRuntimeSettings
from environment_config import load_env_file_values


@dataclass(frozen=True)
class OpenAIActorSettings:
    # OpenAI provider settings remain independent from actor response parsing.
    model: str = "gpt-5.4-nano"
    api_key: str | None = None
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "OpenAIActorSettings":
        # Merge process env and local .env files into one actor settings object.
        env_file_values = load_env_file_values()
        return cls(
            model=os.getenv(
                "LAYER2_ACTOR_OPENAI_MODEL",
                env_file_values.get("LAYER2_ACTOR_OPENAI_MODEL", "gpt-5.4-nano"),
            ),
            api_key=os.getenv("OPENAI_API_KEY", env_file_values.get("OPENAI_API_KEY")),
            timeout_seconds=float(
                os.getenv(
                    "LAYER2_ACTOR_OPENAI_TIMEOUT_SECONDS",
                    env_file_values.get("LAYER2_ACTOR_OPENAI_TIMEOUT_SECONDS", "30"),
                )
            ),
        )


@dataclass(frozen=True)
class BedrockNovaActorSettings:
    # Bedrock actor configuration is typed and never exposed to prompts.
    model_id: str = "eu.amazon.nova-2-lite-v1:0"
    region_name: str = "eu-central-1"
    profile_name: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    session_token: str | None = None
    read_timeout_seconds: float = 3600.0
    connect_timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "BedrockNovaActorSettings":
        # Reuse shared Bedrock environment parsing while keeping the actor testable.
        settings = BedrockRuntimeSettings.from_env()
        return cls(
            model_id=settings.model_id,
            region_name=settings.region_name,
            profile_name=settings.profile_name,
            access_key_id=settings.access_key_id,
            secret_access_key=settings.secret_access_key,
            session_token=settings.session_token,
            read_timeout_seconds=settings.read_timeout_seconds,
            connect_timeout_seconds=settings.connect_timeout_seconds,
        )


# Resolve only the actor provider so planner and replanner remain independent.
def resolve_actor_provider() -> str:
    env_file_values = load_env_file_values()
    return os.getenv(
        "LAYER2_ACTOR_PROVIDER",
        env_file_values.get("LAYER2_ACTOR_PROVIDER", "bedrock"),
    ).strip().lower()


__all__ = ["BedrockNovaActorSettings", "OpenAIActorSettings", "resolve_actor_provider"]
