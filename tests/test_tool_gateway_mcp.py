from __future__ import annotations

import unittest
from uuid import uuid4
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

REPO_ROOT = Path(__file__).resolve().parents[1]

from layer1_intake import (
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    StructuredTask,
    TaskSpec,
)
from layer2_runtime_governance import (
    ActorPort,
    ActorResult,
    ActorResultType,
    ActorTask,
    CapabilityAccessMode,
    GovernanceReasonCode,
    Layer2RuntimeDependencies,
    PlanStep,
    PlannerDecision,
    PolicyDecision,
    PolicyEnginePort,
    PolicyEvaluationInput,
    PolicyEvaluationResult,
    ReplanDecision,
    ReplannerPort,
    RuntimeState,
    ScratchpadEntry,
    ScratchpadEntryType,
    StepValidation,
    ToolGatewayConfig,
    ToolName,
    ToolRequest,
    compose_layer2_runtime,
)
from layer3_tool_bus_external_capabilities.tools import (
    OpenAIToolGateway,
    ToolBackend,
    ToolCapability,
    ToolDefinition,
    ToolRiskLevel,
    load_tool_gateway_config,
)
from layer3_tool_bus_external_capabilities.tool_contract import (
    RetrievalKind,
    ToolErrorCode,
)
from layer4_state_evidence_observability.infrastructure.checkpointing import (
    build_runtime_serializer,
)


MCP_TEST_CONFIG_PATH = REPO_ROOT / "configs" / "layer2_tool_gateway.test.json"


# Build the gateway from the test-only MCP registry instead of polluting runtime defaults.
def _build_mcp_test_gateway() -> OpenAIToolGateway:
    return OpenAIToolGateway(
        config=load_tool_gateway_config(config_path=MCP_TEST_CONFIG_PATH)
    )


class GatewayMCPTests(unittest.TestCase):
    def test_default_config_loads_mcp_tools(self) -> None:
        config = load_tool_gateway_config(config_path=MCP_TEST_CONFIG_PATH)

        self.assertIn(ToolName.MCP_LOCAL_TEST_ECHO, config.tools)
        self.assertIn("local_test", config.mcp_servers)
        self.assertIn("echo", config.mcp_servers["local_test"].allowed_tools)

    def test_mcp_echo_tool_executes_through_gateway(self) -> None:
        gateway = _build_mcp_test_gateway()

        result = gateway.execute(
            tool_request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.MCP_LOCAL_TEST_ECHO,
                reason="Check MCP connectivity through the governed gateway.",
                text="hello mcp",
            )
        )

        self.assertTrue(result.success)
        self.assertEqual(result.content, "hello mcp")
        self.assertEqual(result.observations[0].locator, "mcp://local_test/echo")

    def test_mcp_text_stats_tool_executes_through_gateway(self) -> None:
        gateway = _build_mcp_test_gateway()

        result = gateway.execute(
            tool_request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.MCP_LOCAL_TEST_TEXT_STATS,
                reason="Measure deterministic text statistics.",
                text="DMA DMA gatekeeper",
                limit=2,
            )
        )

        self.assertTrue(result.success)
        self.assertIn('"words": 3', result.content or "")
        self.assertIn('"dma", 2', result.content or "")

    def test_mcp_calculate_expression_blocks_unsupported_syntax(self) -> None:
        gateway = _build_mcp_test_gateway()

        result = gateway.execute(
            tool_request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.MCP_LOCAL_TEST_CALCULATE_EXPRESSION,
                reason="Unsafe expressions should stay blocked by the fixture MCP tool.",
                expression="__import__('os').system('whoami')",
            )
        )

        self.assertFalse(result.success)
        self.assertIn("unsupported syntax", result.error_message or "")

    def test_mcp_read_fixture_is_limited_to_fixture_directory(self) -> None:
        gateway = _build_mcp_test_gateway()

        allowed = gateway.execute(
            tool_request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.MCP_LOCAL_TEST_READ_FIXTURE,
                reason="Read a configured test fixture.",
                path="mcp_fixture.txt",
            )
        )
        blocked = gateway.execute(
            tool_request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.MCP_LOCAL_TEST_READ_FIXTURE,
                reason="Traversal should be rejected by the fixture server.",
                path="..\\layer2_runtime_fixtures.py",
            )
        )

        self.assertTrue(allowed.success)
        self.assertIn("deterministic read-only tools", allowed.content or "")
        self.assertFalse(blocked.success)
        self.assertIn("outside the allowed fixture", blocked.error_message or "")

    def test_not_allowlisted_mcp_tool_returns_structured_failure(self) -> None:
        config = load_tool_gateway_config(config_path=MCP_TEST_CONFIG_PATH)
        original = config.tools[ToolName.MCP_LOCAL_TEST_ECHO]
        blocked_config = ToolGatewayConfig(
            tools={
                **config.tools,
                ToolName.MCP_LOCAL_TEST_ECHO: ToolDefinition(
                    tool_name=original.tool_name,
                    backend=original.backend,
                    capability=original.capability,
                    risk_level=original.risk_level,
                    enabled=original.enabled,
                    requires_human_review=original.requires_human_review,
                    timeout_seconds=original.timeout_seconds,
                    required_fields=original.required_fields,
                    max_content_chars=original.max_content_chars,
                    max_bytes=original.max_bytes,
                    allowed_schemes=original.allowed_schemes,
                    mcp_server_id=original.mcp_server_id,
                    mcp_tool_name="not_allowlisted",
                ),
            },
            mcp_servers=config.mcp_servers,
        )
        gateway = OpenAIToolGateway(config=blocked_config)

        result = gateway.execute(
            tool_request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.MCP_LOCAL_TEST_ECHO,
                reason="Tool allowlist should be enforced.",
                text="blocked",
            )
        )

        self.assertFalse(result.success)
        self.assertIn("not allowlisted", result.error_message or "")

    def test_missing_required_field_returns_structured_failure(self) -> None:
        gateway = _build_mcp_test_gateway()

        result = gateway.execute(
            tool_request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.MCP_LOCAL_TEST_TEXT_STATS,
                reason="Text is required for this tool.",
            )
        )

        self.assertFalse(result.success)
        self.assertIn("requires 'text'", result.error_message or "")

    def test_fetch_url_blocks_unsafe_hosts_before_network(self) -> None:
        gateway = OpenAIToolGateway()

        result = gateway.execute(
            tool_request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.FETCH_URL_TEXT,
                reason="Localhost fetches must be blocked.",
                url="https://localhost/private",
            )
        )

        self.assertFalse(result.success)
        self.assertIn("unsafe host", result.error_message or "")

    def test_fetch_url_accepts_mocked_https_response(self) -> None:
        gateway = OpenAIToolGateway()
        response = FakeHTTPResponse(
            body=(
                b"<html><title>Example</title><body><p>Hello source with enough readable "
                b"content to become meaningful evidence for the runtime.</p></body></html>"
            ),
            content_type="text/html",
        )

        with patch("layer3_tool_bus_external_capabilities.tools.security.is_blocked_host", return_value=False):
            with patch("layer3_tool_bus_external_capabilities.tools.adapters.http_fetch.urlopen", return_value=response):
                result = gateway.execute(
                    tool_request=ToolRequest(
                        plan_step_id="step_1",
                        tool_name=ToolName.FETCH_URL_TEXT,
                        reason="Fetch one mocked HTTPS document.",
                        url="https://example.org/doc",
                    )
                )

        self.assertTrue(result.success)
        self.assertEqual(result.observations[0].locator, "https://example.org/doc")
        self.assertEqual(
            result.observations[0].retrieval_kind,
            RetrievalKind.FETCHED_DOCUMENT,
        )
        self.assertIn("Hello source", result.content or "")

    # Browser, consent, and bot interstitials are retryable extraction failures.
    def test_fetch_url_detects_client_challenge_marker(self) -> None:
        gateway = OpenAIToolGateway()
        response = FakeHTTPResponse(
            body=(
                b"<html><body>Client Challenge A required part of this site could not "
                b"load. Please enable JavaScript to continue.</body></html>"
            ),
            content_type="text/html",
        )

        with patch(
            "layer3_tool_bus_external_capabilities.tools.security.is_blocked_host",
            return_value=False,
        ), patch(
            "layer3_tool_bus_external_capabilities.tools.adapters.http_fetch.urlopen",
            return_value=response,
        ):
            result = gateway.execute(
                tool_request=ToolRequest(
                    plan_step_id="step_1",
                    tool_name=ToolName.FETCH_URL_TEXT,
                    reason="Fetch a document behind a client challenge.",
                    url="https://example.org/challenge",
                )
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ToolErrorCode.BOT_CHALLENGE)
        self.assertTrue(result.extractability_limited)

    # HTML Last-Modified is transport metadata, not semantic publication evidence.
    def test_fetch_url_excludes_html_last_modified_from_publication_timestamp(self) -> None:
        gateway = OpenAIToolGateway()
        response = FakeHTTPResponse(
            body=(
                b"<html><body><p>Readable source content that is long enough to become "
                b"meaningful evidence for the runtime.</p></body></html>"
            ),
            content_type="text/html",
            last_modified="Fri, 22 Aug 2026 10:30:00 GMT",
        )

        with patch(
            "layer3_tool_bus_external_capabilities.tools.security.is_blocked_host",
            return_value=False,
        ), patch(
            "layer3_tool_bus_external_capabilities.tools.adapters.http_fetch.urlopen",
            return_value=response,
        ):
            result = gateway.execute(
                tool_request=ToolRequest(
                    plan_step_id="step_1",
                    tool_name=ToolName.FETCH_URL_TEXT,
                    reason="Fetch source metadata.",
                    url="https://example.org/doc",
                )
            )

        self.assertTrue(result.success)
        self.assertIsNone(result.observations[0].source_timestamp)
        self.assertIn(
            "excluded from the source publication timestamp",
            result.summary,
        )

    # Transport timestamps cannot satisfy editorial freshness for plain text either.
    def test_fetch_url_excludes_plain_text_last_modified_from_publication_timestamp(
        self,
    ) -> None:
        gateway = OpenAIToolGateway()
        response = FakeHTTPResponse(
            body=(
                b"Readable plain-text source content that is long enough to become "
                b"meaningful evidence for the runtime."
            ),
            content_type="text/plain",
            last_modified="Fri, 22 Aug 2026 10:30:00 GMT",
        )

        with patch(
            "layer3_tool_bus_external_capabilities.tools.security.is_blocked_host",
            return_value=False,
        ), patch(
            "layer3_tool_bus_external_capabilities.tools.adapters.http_fetch.urlopen",
            return_value=response,
        ):
            result = gateway.execute(
                tool_request=ToolRequest(
                    plan_step_id="step_1",
                    tool_name=ToolName.FETCH_URL_TEXT,
                    reason="Fetch plain-text source metadata.",
                    url="https://example.org/source.txt",
                )
            )

        self.assertTrue(result.success)
        self.assertIsNone(result.observations[0].source_timestamp)
        self.assertIn(
            "excluded from the source publication timestamp",
            result.summary,
        )

    # Semantic publication metadata overrides a conflicting HTTP modification time.
    def test_fetch_url_prefers_html_publication_metadata(self) -> None:
        gateway = OpenAIToolGateway()
        response = FakeHTTPResponse(
            body=(
                b"<html><head><meta property='article:published_time' "
                b"content='2026-08-06T09:15:00Z'></head><body><p>Readable source "
                b"content that is long enough to become meaningful evidence.</p></body></html>"
            ),
            content_type="text/html",
            last_modified="Fri, 22 Aug 2026 10:30:00 GMT",
        )

        with patch(
            "layer3_tool_bus_external_capabilities.tools.security.is_blocked_host",
            return_value=False,
        ), patch(
            "layer3_tool_bus_external_capabilities.tools.adapters.http_fetch.urlopen",
            return_value=response,
        ):
            result = gateway.execute(
                tool_request=ToolRequest(
                    plan_step_id="step_1",
                    tool_name=ToolName.FETCH_URL_TEXT,
                    reason="Fetch semantic publication metadata.",
                    url="https://example.org/openai-like-article",
                )
            )

        self.assertTrue(result.success)
        self.assertEqual(
            result.observations[0].source_timestamp.isoformat(),
            "2026-08-06T09:15:00+00:00",
        )
        self.assertIn("html_meta:article:published_time", result.summary)

    # XHTML carries the same semantic publication metadata contract as HTML.
    def test_fetch_url_extracts_xhtml_publication_metadata(self) -> None:
        gateway = OpenAIToolGateway()
        response = FakeHTTPResponse(
            body=(
                b"<html xmlns='http://www.w3.org/1999/xhtml'><head>"
                b"<meta property='article:published_time' content='2026-08-07T11:00:00Z' />"
                b"</head><body><p>Readable XHTML source content that is long enough "
                b"to become meaningful evidence.</p></body></html>"
            ),
            content_type="application/xhtml+xml",
            last_modified="Fri, 22 Aug 2026 10:30:00 GMT",
        )

        with patch(
            "layer3_tool_bus_external_capabilities.tools.security.is_blocked_host",
            return_value=False,
        ), patch(
            "layer3_tool_bus_external_capabilities.tools.adapters.http_fetch.urlopen",
            return_value=response,
        ):
            result = gateway.execute(
                tool_request=ToolRequest(
                    plan_step_id="step_1",
                    tool_name=ToolName.FETCH_URL_TEXT,
                    reason="Fetch semantic XHTML publication metadata.",
                    url="https://example.org/openai-like-article.xhtml",
                )
            )

        self.assertTrue(result.success)
        self.assertEqual(
            result.observations[0].source_timestamp.isoformat(),
            "2026-08-07T11:00:00+00:00",
        )
        self.assertIn("html_meta:article:published_time", result.summary)

    def test_fetch_url_empty_extracted_text_returns_failure(self) -> None:
        gateway = OpenAIToolGateway()
        response = FakeHTTPResponse(
            body=b"<html><title>Empty</title><body><script>hidden()</script></body></html>",
            content_type="text/html",
        )

        with patch("layer3_tool_bus_external_capabilities.tools.security.is_blocked_host", return_value=False):
            with patch("layer3_tool_bus_external_capabilities.tools.adapters.http_fetch.urlopen", return_value=response):
                result = gateway.execute(
                    tool_request=ToolRequest(
                        plan_step_id="step_1",
                        tool_name=ToolName.FETCH_URL_TEXT,
                        reason="Fetch one mocked empty HTTPS document.",
                        url="https://example.org/empty",
                    )
                )

        self.assertFalse(result.success)
        self.assertIn("extracted readable text was empty", result.error_message or "")

    # Verify a JS-gated EUR-Lex page falls back to official machine-readable CELLAR content.
    def test_fetch_url_uses_cellar_fallback_for_empty_eur_lex_extraction(self) -> None:
        gateway = OpenAIToolGateway()
        eur_lex_response = FakeHTTPResponse(
            body=(
                b"<html><title>JavaScript is disabled</title><body>"
                b"In order to continue, we need to verify that you're not a robot."
                b"</body></html>"
            ),
            content_type="text/html",
        )
        cellar_response = FakeHTTPResponse(
            body=b"%PDF-1.7 mock",
            content_type="application/pdf",
            last_modified="Fri, 22 Aug 2026 10:30:00 GMT",
        )

        with patch("layer3_tool_bus_external_capabilities.tools.security.is_blocked_host", return_value=False):
            with patch(
                "layer3_tool_bus_external_capabilities.tools.adapters.http_fetch.urlopen",
                side_effect=[eur_lex_response, cellar_response],
            ), patch(
                "layer3_tool_bus_external_capabilities.tools.adapters.http_fetch.extract_pdf_text",
                return_value=(
                    "The purpose of this Regulation is to contribute to the proper "
                    "functioning of the internal market by laying down harmonised rules."
                    + (" Additional official provision." * 2000)
                ),
            ):
                result = gateway.execute(
                    tool_request=ToolRequest(
                        plan_step_id="step_1",
                        tool_name=ToolName.FETCH_URL_TEXT,
                        reason="Retrieve the official DMA legal text.",
                        url=(
                            "https://eur-lex.europa.eu/legal-content/EN/TXT/?"
                            "uri=CELEX%3A02022R1925-20221012"
                        ),
                    )
                )

        self.assertTrue(result.success)
        self.assertTrue(result.truncated)
        self.assertTrue(result.extractability_limited)
        self.assertIn("publication fallback", result.summary)
        self.assertIn("proper functioning of the internal market", result.content or "")
        self.assertEqual(len(result.observations), 1)
        self.assertIn(
            "proper functioning of the internal market",
            result.observations[0].content,
        )
        self.assertEqual(
            result.observations[0].locator,
            "http://publications.europa.eu/resource/celex/32022R1925",
        )
        self.assertIsNone(result.observations[0].source_timestamp)

    def test_disabled_tool_returns_structured_failure(self) -> None:
        disabled_config = ToolGatewayConfig(
            tools={
                ToolName.MCP_LOCAL_TEST_ECHO: ToolDefinition(
                    tool_name=ToolName.MCP_LOCAL_TEST_ECHO,
                    backend=ToolBackend.MCP,
                    capability=ToolCapability.READ,
                    risk_level=ToolRiskLevel.LOW,
                    enabled=False,
                    requires_human_review=False,
                    timeout_seconds=5,
                    required_fields=("text",),
                    mcp_server_id="local_test",
                    mcp_tool_name="echo",
                )
            }
        )
        gateway = OpenAIToolGateway(config=disabled_config)

        result = gateway.execute(
            tool_request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.MCP_LOCAL_TEST_ECHO,
                reason="Disabled tools should not execute.",
                text="nope",
            )
        )

        self.assertFalse(result.success)
        self.assertIn("not registered or is disabled", result.error_message or "")

    def test_runtime_observes_mcp_tool_result(self) -> None:
        graph = compose_layer2_runtime(
            Layer2RuntimeDependencies(
                planner=SingleStepPlanner(),
                actor=MCPThenCompleteActor(),
                tool_gateway=_build_mcp_test_gateway(),
                replanner=NoopReplanner(),
                policy_engine=AllowPolicyEngine(),
                checkpointer=InMemorySaver(serde=build_runtime_serializer()),
            )
        )

        result = graph.invoke(
            {"intake_result": build_research_intake_result()},
            # Durable checkpoints require a unique id so repeated test runs remain isolated.
            config={"configurable": {"thread_id": f"mcp-runtime-tool-loop-{uuid4()}"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(task_state.tool_call_count, 1)
        self.assertEqual(len(task_state.observations), 1)
        self.assertIn("agent", task_state.observations[0].content)


class FakeHTTPHeaders:
    def __init__(
        self,
        content_type: str,
        last_modified: str | None = None,
    ) -> None:
        self._content_type = content_type
        self._last_modified = last_modified

    # Return the mocked response charset.
    def get_content_charset(self) -> str:
        return "utf-8"

    # Return the mocked response content type.
    def get_content_type(self) -> str:
        return self._content_type

    # Return a source header when the test fixture declares one.
    def get(self, name: str) -> str | None:
        return self._last_modified if name.lower() == "last-modified" else None


class FakeHTTPResponse:
    def __init__(
        self,
        *,
        body: bytes,
        content_type: str,
        last_modified: str | None = None,
    ) -> None:
        self._body = body
        self.headers = FakeHTTPHeaders(content_type, last_modified)

    # Enter the context manager used by urlopen.
    def __enter__(self) -> "FakeHTTPResponse":
        return self

    # Exit the context manager used by urlopen.
    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    # Return the mocked response body.
    def read(self, size: int = -1) -> bytes:
        return self._body if size == -1 else self._body[:size]


class SingleStepPlanner:
    planner_name = "single_step_planner"
    prompt_version = "single_step_v1"

    # Return one semantic step so the test focuses on the MCP tool loop.
    def plan(self, *, task_state):
        return PlannerDecision(
            plan_steps=[
                PlanStep(
                    step_id="step_1",
                    objective="Use one MCP tool and finish.",
                    rationale="The test validates the governed MCP tool path.",
                    expected_outcome="Observed evidence from an MCP tool.",
                )
            ],
            rationale="Single-step MCP runtime test.",
            planner_name=self.planner_name,
            prompt_version=self.prompt_version,
        )


class MCPThenCompleteActor(ActorPort):
    actor_name = "mcp_then_complete_actor"
    prompt_version = "mcp_then_complete_v1"

    # Request an MCP tool once, then complete after observing evidence.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        has_mcp_observation = bool(actor_task.observations)
        if not has_mcp_observation:
            return ActorResult(
                plan_step_id=actor_task.current_plan_step.step_id,
                result_type=ActorResultType.PROPOSE_ACTION,
                summary="Request MCP text statistics.",
                capability_mode=CapabilityAccessMode.READ_ONLY,
                external_impact=False,
                tool_request=ToolRequest(
                    plan_step_id=actor_task.current_plan_step.step_id,
                    tool_name=ToolName.MCP_LOCAL_TEST_TEXT_STATS,
                    reason="The step needs deterministic text statistics.",
                    text="agent agent governance",
                    limit=2,
                ),
                scratchpad_entry=ScratchpadEntry(
                    turn_index=actor_task.actor_turn_count,
                    plan_step_id=actor_task.current_plan_step.step_id,
                    entry_type=ScratchpadEntryType.TOOL_DECISION,
                    summary="MCP text statistics are enough for the test step.",
                ),
            )

        return ActorResult(
            plan_step_id=actor_task.current_plan_step.step_id,
            result_type=ActorResultType.STEP_COMPLETED,
            summary="MCP evidence observed.",
            capability_mode=CapabilityAccessMode.NONE,
            external_impact=False,
            output_text="The MCP tool returned deterministic text statistics.",
            step_validation=StepValidation(
                plan_step_id=actor_task.current_plan_step.step_id,
                actor_self_confidence=0.9,
                evidence_refs=tuple(
                    item.observation_id for item in actor_task.observations
                ),
                rationale_summary="The MCP result was observed and normalized as evidence.",
            ),
        )


class AllowPolicyEngine(PolicyEnginePort):
    policy_engine_name = "allow_policy_engine"
    policy_bundle_version = "layer2_governance_v5"
    policy_config_version = "layer2_governance_thresholds_v2"

    # Request deterministic pillars without invoking a semantic judge in this tool test.
    def evaluate_required_pillars(self, *, base_facts):
        return {
            "require_evidence": True,
            "require_risk": False,
            "require_trajectory": True,
            "require_behavioral": False,
            "require_judge": False,
        }

    # Return a schema-valid decision so this test remains focused on the tool loop.
    def evaluate_decision(self, *, decision_input):
        has_tool = decision_input["base_facts"]["tool"]["name"] is not None
        pillar_results = dict(decision_input["pillars"])
        pillar_results["static_policy"] = {
            "hard_deny": False,
            "constraints": ["execution_policy_read_only"],
            "violations": [],
            "reason_codes": ["read_only_allowed"],
            "summary": "Static policy preserved the read-only execution envelope.",
            "read_only_required": True,
            "clarification_signaled": False,
            "mode": "active",
            "decision_influence": True,
        }
        return {
            "schema_version": "governance_decision_output.v1",
            "directive": "continue_read_only" if has_tool else "continue",
            "policy_decision": "allow_read_only" if has_tool else "allow",
            "reason_codes": ["read_only_allowed" if has_tool else "allowed_by_policy"],
            "judge_admission": {
                "required": False,
                "reason": "deterministic_gate_not_admissible",
            },
            "pillar_results": pillar_results,
            "selected_reason": "Read-only runtime test action is allowed.",
            "rejected_alternatives": {},
            "safety_invariants_checked": ["read_only_envelope_preserved"],
            "requires_human_review": False,
            "policy_bundle_version": self.policy_bundle_version,
            "policy_config_version": self.policy_config_version,
        }

    # Allow all read-only actions in this focused runtime test.
    def evaluate(self, *, policy_input: PolicyEvaluationInput) -> PolicyEvaluationResult:
        return PolicyEvaluationResult(
            policy_decision=(
                PolicyDecision.ALLOW_READ_ONLY
                if policy_input.tool_name is not None
                else PolicyDecision.ALLOW
            ),
            reason_codes=(GovernanceReasonCode.READ_ONLY_ALLOWED,),
            summary="Read-only runtime test action is allowed.",
            policy_engine_name=self.policy_engine_name,
            policy_bundle_version=self.policy_bundle_version,
        )


class NoopReplanner(ReplannerPort):
    replanner_name = "noop_replanner"
    prompt_version = "noop_v1"

    # Return an empty replacement because this test never reaches replanning.
    def replan(self, *, task_state) -> ReplanDecision:
        return ReplanDecision(
            replacement_steps=[],
            rationale="No replanning expected.",
            replanner_name=self.replanner_name,
            prompt_version=self.prompt_version,
        )


# Build one low-risk intake result for the focused runtime test.
def build_research_intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Use a local MCP test tool.",
                context="Runtime test.",
                constraints=[],
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Produce normalized MCP evidence."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.65,
                read_only_mode=True,
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.COMPLETE,
            parser_name="stub_parser",
            prompt_version="test_v1",
            schema_version="1.0.0",
        ),
    )


if __name__ == "__main__":
    unittest.main()
