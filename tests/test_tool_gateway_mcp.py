from __future__ import annotations

import sys
import unittest
from uuid import uuid4
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from layer1_intake import (
    BudgetConfig,
    DomainType,
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
    StepValidationDecision,
    ToolExecutionResult,
    ToolGatewayConfig,
    ToolName,
    ToolRequest,
    build_runtime_graph,
)
from layer2_runtime_governance.tools import (
    OpenAIToolGateway,
    ToolBackend,
    ToolCapability,
    ToolDefinition,
    ToolRiskLevel,
    load_tool_gateway_config,
)
from layer2_runtime_governance.infrastructure.checkpointing import (
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
        self.assertEqual(result.sources[0].locator, "mcp://local_test/echo")

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

        with patch("layer2_runtime_governance.tools.security.is_blocked_host", return_value=False):
            with patch("layer2_runtime_governance.tools.adapters.http_fetch.urlopen", return_value=response):
                result = gateway.execute(
                    tool_request=ToolRequest(
                        plan_step_id="step_1",
                        tool_name=ToolName.FETCH_URL_TEXT,
                        reason="Fetch one mocked HTTPS document.",
                        url="https://example.org/doc",
                    )
                )

        self.assertTrue(result.success)
        self.assertEqual(result.sources[0].title, "Example")
        self.assertIn("Hello source", result.content or "")

    def test_fetch_url_empty_extracted_text_returns_failure(self) -> None:
        gateway = OpenAIToolGateway()
        response = FakeHTTPResponse(
            body=b"<html><title>Empty</title><body><script>hidden()</script></body></html>",
            content_type="text/html",
        )

        with patch("layer2_runtime_governance.tools.security.is_blocked_host", return_value=False):
            with patch("layer2_runtime_governance.tools.adapters.http_fetch.urlopen", return_value=response):
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
        cellar_response = FakeHTTPResponse(body=b"%PDF-1.7 mock", content_type="application/pdf")

        with patch("layer2_runtime_governance.tools.security.is_blocked_host", return_value=False):
            with patch(
                "layer2_runtime_governance.tools.adapters.http_fetch.urlopen",
                side_effect=[eur_lex_response, cellar_response],
            ), patch(
                "layer2_runtime_governance.tools.adapters.http_fetch.extract_pdf_text",
                return_value=(
                    "The purpose of this Regulation is to contribute to the proper "
                    "functioning of the internal market by laying down harmonised rules."
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
        self.assertIn("CELLAR fallback", result.summary)
        self.assertIn("proper functioning of the internal market", result.content or "")
        self.assertEqual(
            result.sources[0].locator,
            "http://publications.europa.eu/resource/celex/32022R1925",
        )

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
        graph = build_runtime_graph(
            planner_service=SingleStepPlanner(),
            actor_service=MCPThenCompleteActor(),
            tool_gateway_service=_build_mcp_test_gateway(),
            replanner_service=NoopReplanner(),
            policy_engine_service=AllowPolicyEngine(),
            checkpointer=InMemorySaver(serde=build_runtime_serializer()),
        )

        result = graph.invoke(
            {"intake_result": build_research_intake_result()},
            # Durable checkpoints require a unique id so repeated test runs remain isolated.
            config={"configurable": {"thread_id": f"mcp-runtime-tool-loop-{uuid4()}"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(task_state.tool_call_count, 1)
        tool_observations = [
            evidence
            for evidence in task_state.observed_evidence
            if evidence.source_tool is not None
        ]
        self.assertEqual(len(tool_observations), 1)
        self.assertEqual(
            tool_observations[0].source_tool,
            ToolName.MCP_LOCAL_TEST_TEXT_STATS,
        )


class FakeHTTPHeaders:
    def __init__(self, content_type: str) -> None:
        self._content_type = content_type

    # Return the mocked response charset.
    def get_content_charset(self) -> str:
        return "utf-8"

    # Return the mocked response content type.
    def get_content_type(self) -> str:
        return self._content_type


class FakeHTTPResponse:
    def __init__(self, *, body: bytes, content_type: str) -> None:
        self._body = body
        self.headers = FakeHTTPHeaders(content_type)

    # Enter the context manager used by urlopen.
    def __enter__(self) -> "FakeHTTPResponse":
        return self

    # Exit the context manager used by urlopen.
    def __exit__(self, exc_type, exc, traceback) -> None:
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
        has_mcp_observation = any(
            evidence.source_tool == ToolName.MCP_LOCAL_TEST_TEXT_STATS
            for evidence in actor_task.observed_evidence
        )
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
            result_type=ActorResultType.FINAL_OUTPUT,
            summary="MCP evidence observed.",
            capability_mode=CapabilityAccessMode.NONE,
            external_impact=False,
            output_text="The MCP tool returned deterministic text statistics.",
            step_validation=StepValidation(
                plan_step_id=actor_task.current_plan_step.step_id,
                objective_satisfied=True,
                evidence_sufficient=True,
                confidence=0.9,
                next_action=StepValidationDecision.COMPLETE_STEP,
                rationale_summary="The MCP result was observed and normalized as evidence.",
            ),
        )


class AllowPolicyEngine(PolicyEnginePort):
    policy_engine_name = "allow_policy_engine"
    policy_bundle_version = "layer2_governance_v2"
    policy_config_version = "layer2_governance_thresholds_v1"

    # Request deterministic pillars without invoking a semantic judge in this tool test.
    def evaluate_required_pillars(self, *, base_facts):
        return {
            "require_evidence": True,
            "require_risk": True,
            "require_trajectory": True,
            "require_behavioral": False,
            "require_judge": False,
        }

    # Return a schema-valid decision so this test remains focused on the tool loop.
    def evaluate_decision(self, *, decision_input):
        has_tool = decision_input["base_facts"]["tool"]["name"] is not None
        return {
            "schema_version": "governance_decision_output.v1",
            "directive": "continue_read_only" if has_tool else "continue",
            "policy_decision": "allow_read_only" if has_tool else "allow",
            "reason_codes": ["read_only_allowed" if has_tool else "allowed_by_policy"],
            "pillar_results": decision_input["pillars"],
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
                domain_type=DomainType.RESEARCH,
                domain_label="mcp_gateway_test",
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Produce normalized MCP evidence."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.65,
                budget=BudgetConfig(max_steps=3, max_tool_calls=3),
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
