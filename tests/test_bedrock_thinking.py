import os
from unittest.mock import Mock, patch

import pytest

from layer2_runtime_governance.execution.providers.bedrock_runtime import (
    BedrockChatClient,
    BedrockRuntimeError,
    BedrockRuntimeSettings,
    BedrockThinkingMode,
    BedrockThinkingEffort,
)
from layer4_state_evidence_observability.infrastructure.checkpointing import build_runtime_serializer
from layer2_runtime_governance.execution.settings import BedrockNovaActorSettings
from layer2_runtime_governance.execution.providers.bedrock import BedrockNovaActor
from layer2_runtime_governance.runtime.actor_service import actor
from layer4_state_evidence_observability.observability import build_layer2_runtime_trace, layer2_trace_to_dict
from layer5_evaluation_continuous_improvement.ingestion import _configuration_fingerprint
from tests.layer2_runtime_fixtures import _build_policy_input_task_state


# Exercise the actual Converse envelope and ensure reasoning never enters the action payload.
@pytest.mark.parametrize("mode", ["off", "adaptive"])
def test_claude_thinking_request_and_response(mode):
    settings = BedrockRuntimeSettings(
        model_id="eu.anthropic.claude-sonnet-4-6", thinking=mode, profile_name="test"
    )
    with patch("layer2_runtime_governance.execution.providers.bedrock_runtime.boto3"):
        client = BedrockChatClient(settings)
    client._client = Mock()
    client._client.converse.return_value = {"output": {"message": {"content": [
        {"reasoningContent": {"reasoningText": {"text": "private", "signature": "secret"}}},
        {"toolUse": {"name": "emit_step_completed", "input": {"summary": "done"}}},
    ]}}}
    result = client.complete_tool_calls(system_prompt="system", user_prompt="user", tools=[])
    assert result == ({"tool_name": "emit_step_completed", "arguments": {"summary": "done"}},)
    request = client._client.converse.call_args.kwargs
    assert request["toolConfig"]["toolChoice"] == {"auto" if mode == "adaptive" else "any": {}}
    if mode == "adaptive":
        assert request["additionalModelRequestFields"] == {
            "thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}
        }
    else:
        assert "additionalModelRequestFields" not in request
    client._client.converse.return_value = {"output": {"message": {"content": [{"text": "done"}]}}}
    with pytest.raises(BedrockRuntimeError, match="no tool use"):
        client.complete_tool_calls(system_prompt="system", user_prompt="user", tools=[])


# Exercise Nova's distinct reasoning envelope without changing its forced tool selection.
def test_nova_extended_thinking_request_and_response():
    settings = BedrockRuntimeSettings(
        model_id="eu.amazon.nova-2-lite-v1:0",
        thinking="extended",
        thinking_effort="high",
        profile_name="test",
    )
    with patch("layer2_runtime_governance.execution.providers.bedrock_runtime.boto3"):
        client = BedrockChatClient(settings)
    client._client = Mock()
    client._client.converse.return_value = {"output": {"message": {"content": [
        {"reasoningContent": {"reasoningText": {"text": "[REDACTED]"}}},
        {"toolUse": {"name": "emit_step_completed", "input": {"summary": "done"}}},
    ]}}}

    result = client.complete_tool_calls(system_prompt="system", user_prompt="user", tools=[])

    assert result == ({"tool_name": "emit_step_completed", "arguments": {"summary": "done"}},)
    request = client._client.converse.call_args.kwargs
    assert request["toolConfig"]["toolChoice"] == {"any": {}}
    assert request["additionalModelRequestFields"] == {
        "reasoningConfig": {"type": "enabled", "maxReasoningEffort": "high"}
    }


# Archived checkpoints from the first thinking campaign must remain readable without warnings.
def test_legacy_thinking_checkpoint_roundtrip(caplog):
    state = _build_policy_input_task_state()
    state.actor_thinking_mode = BedrockThinkingMode.ADAPTIVE
    state.actor_thinking_effort = BedrockThinkingEffort.HIGH
    serializer = build_runtime_serializer()
    restored = serializer.loads_typed(serializer.dumps_typed(state))
    assert restored.actor_thinking_mode == "adaptive"
    assert restored.actor_thinking_effort == "high"
    assert "Blocked deserialization" not in caplog.text


# Environment selection must reach the concrete adapter and take precedence over env files.
def test_environment_wiring():
    with patch.dict(os.environ, {
        "LAYER2_ACTOR_BEDROCK_MODEL_ID": "eu.anthropic.claude-sonnet-4-6",
        "LAYER2_ACTOR_BEDROCK_THINKING": "adaptive",
        "LAYER2_ACTOR_BEDROCK_THINKING_EFFORT": "medium",
    }, clear=True), patch(
        "layer2_runtime_governance.execution.providers.bedrock_runtime._load_env_file_values",
        return_value={"LAYER2_ACTOR_BEDROCK_THINKING": "off"},
    ), patch("layer2_runtime_governance.execution.providers.bedrock.BedrockChatClient") as client:
        service = BedrockNovaActor(BedrockNovaActorSettings.from_env())
    assert client.call_args.args[0].thinking == "adaptive"
    assert client.call_args.args[0].thinking_effort == "medium"
    assert service.thinking_effort == "medium"
    assert type(service.thinking_mode) is str
    assert type(service.thinking_effort) is str


# Reject unsupported configuration before any network client can be constructed.
@pytest.mark.parametrize("kwargs", [
    {"thinking": "enabled"},
    {"thinking_effort": "xhigh"},
    {"thinking": "adaptive"},
    {"thinking": "extended", "model_id": "eu.anthropic.claude-sonnet-4-6"},
    {"thinking": "extended", "thinking_effort": "max"},
])
def test_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        BedrockRuntimeSettings(**kwargs)


# Preserve the Nova reasoning mode and effective effort in actor-facing metadata.
def test_nova_extended_thinking_metadata():
    with patch("layer2_runtime_governance.execution.providers.bedrock.BedrockChatClient"):
        service = BedrockNovaActor(BedrockNovaActorSettings(
            model_id="eu.amazon.nova-2-lite-v1:0",
            thinking="extended",
            thinking_effort="high",
        ))

    assert service.thinking_mode == "extended"
    assert service.thinking_effort == "high"


# Persist actual adapter configuration even when the first provider request fails.
def test_runtime_metadata_and_fingerprint():
    state = _build_policy_input_task_state()
    with patch("layer2_runtime_governance.execution.providers.bedrock.BedrockChatClient"):
        service = BedrockNovaActor(BedrockNovaActorSettings(
            model_id="eu.anthropic.claude-sonnet-4-6", thinking="adaptive"
        ))
    service.act = Mock(side_effect=RuntimeError("provider unavailable"))
    with pytest.raises(RuntimeError, match="provider unavailable"):
        actor(state, actor_service=service)
    payload = layer2_trace_to_dict(build_layer2_runtime_trace(
        thread_id="thinking-test", intake_result=state.intake_payload, task_state=state
    ))
    assert payload["actor"]["actor_thinking_mode"] == "adaptive"
    assert payload["actor"]["actor_model_id"] == service.model_id
    assert type(state.actor_thinking_mode) is str
    assert type(state.actor_thinking_effort) is str
    fingerprint = _configuration_fingerprint(payload)
    payload["actor"]["actor_thinking_effort"] = "low"
    assert _configuration_fingerprint(payload) != fingerprint
    legacy = {"actor": {}}
    unknown = {"actor": {"actor_model_id": None, "actor_thinking_mode": None, "actor_thinking_effort": None}}
    assert _configuration_fingerprint(legacy) == _configuration_fingerprint(unknown)
    state.actor_thinking_mode = "off"
    with pytest.raises(ValueError, match="fresh thread"):
        actor(state, actor_service=service)
