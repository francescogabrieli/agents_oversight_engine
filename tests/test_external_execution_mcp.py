from __future__ import annotations

import json
import os
import shutil
import socketserver
import sqlite3
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from mcp.types import CallToolResult, TextContent

from layer1_intake import ExecutionIntent
from layer2_runtime_governance.external_operations import (
    DatabaseOperationParameter,
    ExternalExecutionAuthorizationSource,
    ExternalExecutionErrorCode,
    ExternalExecutionLedger,
    ExternalExecutionStatus,
    build_external_execution_request,
    load_external_capability_registry,
    normalize_communication_operation,
    normalize_sql_operation,
)
from layer2_runtime_governance.external_operations.mcp import (
    MCPCallTimeout,
    MCPConnectionPool,
    MCPExternalOperationExecutor,
    MCPPoolClosedError,
)
from layer2_runtime_governance.domain import ExternalCommunicationDraft


class _FakePool:
    # A bounded fake isolates response parsing from official transport integration.
    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.calls = []

    # Return the configured SDK result or normalized connector exception.
    def call_tool(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result

    # Match the pool lifecycle interface without external resources.
    def close(self) -> None:
        return None


class _CaptureSMTPHandler(socketserver.StreamRequestHandler):
    # Implement the minimal SMTP dialogue needed by smtplib for capture-only tests.
    def handle(self) -> None:
        self.wfile.write(b"220 capture.local ESMTP ready\r\n")
        in_data = False
        message_lines: list[bytes] = []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            if in_data:
                if line == b".\r\n":
                    self.server.messages.append(b"".join(message_lines))  # type: ignore[attr-defined]
                    self.wfile.write(b"250 queued as capture-1\r\n")
                    in_data = False
                    continue
                message_lines.append(line)
                continue
            command = line.decode("utf-8", errors="replace").strip().upper()
            if command.startswith("EHLO") or command.startswith("HELO"):
                self.wfile.write(b"250 capture.local\r\n")
            elif command.startswith("MAIL FROM") or command.startswith("RCPT TO"):
                self.wfile.write(b"250 accepted\r\n")
            elif command == "DATA":
                self.wfile.write(b"354 end with <CRLF>.<CRLF>\r\n")
                in_data = True
            elif command == "QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"250 ok\r\n")


class _CaptureSMTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    # Keep only captured bytes inside the temporary integration process.
    def __init__(self, server_address) -> None:
        super().__init__(server_address, _CaptureSMTPHandler)
        self.messages: list[bytes] = []


# Create one temporary config copy and enable only a selected MCP server.
def _enabled_registry(
    root: Path,
    *,
    capability_id: str,
    extra_environment_keys: tuple[str, ...] = (),
):
    payload = json.loads(
        (REPO_ROOT / "configs" / "layer2_external_capabilities.example.json").read_text(
            encoding="utf-8"
        )
    )
    for capability in payload["capabilities"]:
        capability["enabled"] = capability["capability_id"] == capability_id
    selected_server = next(
        capability["mcp_server_id"]
        for capability in payload["capabilities"]
        if capability["capability_id"] == capability_id
    )
    for server_id, server in payload["mcp_servers"].items():
        server["enabled"] = server_id == selected_server
        if server_id == selected_server:
            server["environment_keys"] = list(
                dict.fromkeys(
                    [*server["environment_keys"], *extra_environment_keys]
                )
            )
    path = root / f"{selected_server}-registry.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_external_capability_registry(config_path=path)


# Create one exact email request for adapter parsing and SMTP integration.
def _email_request(operation_id: str = "email-operation"):
    proposal = normalize_communication_operation(
        operation_id=operation_id,
        plan_step_id="email-step",
        capability_id="communication.email.send",
        draft=ExternalCommunicationDraft(
            channel="email",
            recipient="recipient@example.test",
            subject="Captured integration",
            body="This email must remain in the local capture server.",
        ),
        execution_candidate=True,
    )
    return build_external_execution_request(
        proposal=proposal,
        authorized_payload_digest=proposal.payload_digest,
        execution_intent=ExecutionIntent.EXECUTE_AFTER_GOVERNANCE,
        authorization_source=ExternalExecutionAuthorizationSource.HUMAN_REVIEW,
    )


class MCPExternalExecutorUnitTests(unittest.TestCase):
    # Create and remove one workspace-local temporary directory per test.
    def setUp(self) -> None:
        temp_root = REPO_ROOT / ".tmp"
        temp_root.mkdir(exist_ok=True)
        self.temp_dir = temp_root / f"mcp-unit-{uuid4()}"
        self.temp_dir.mkdir()

    # Remove only the test-owned exact directory.
    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # A malformed MCP body must never be promoted to executed.
    def test_malformed_response_returns_failed_receipt(self) -> None:
        registry = _enabled_registry(
            self.temp_dir,
            capability_id="communication.email.send",
        )
        pool = _FakePool(
            result=CallToolResult(
                content=[TextContent(type="text", text="not-json")],
                isError=False,
            )
        )
        executor = MCPExternalOperationExecutor(
            registry=registry,
            pool=pool,
            ledger=ExternalExecutionLedger(path=self.temp_dir / "client.sqlite3"),
        )
        receipt = executor.execute(_email_request())
        self.assertEqual(receipt.status, ExternalExecutionStatus.FAILED)
        self.assertEqual(
            receipt.error_code,
            ExternalExecutionErrorCode.CONNECTOR_RESPONSE_INVALID,
        )

    # A connector timeout is indeterminate because the remote effect is unknown.
    def test_timeout_returns_indeterminate_receipt(self) -> None:
        registry = _enabled_registry(
            self.temp_dir,
            capability_id="communication.email.send",
        )
        pool = _FakePool(
            error=MCPCallTimeout(
                "fixture timeout",
                outcome_may_be_indeterminate=True,
            )
        )
        executor = MCPExternalOperationExecutor(
            registry=registry,
            pool=pool,
            ledger=ExternalExecutionLedger(path=self.temp_dir / "client.sqlite3"),
        )
        receipt = executor.execute(_email_request())
        self.assertEqual(receipt.status, ExternalExecutionStatus.INDETERMINATE)
        self.assertFalse(receipt.retryable)

    # Adapter validation blocks a mapping whose exact tool is not allowlisted.
    def test_non_allowlisted_mapping_is_rejected_before_pool_call(self) -> None:
        registry = _enabled_registry(
            self.temp_dir,
            capability_id="communication.email.send",
        )
        server = registry.mcp_servers["communication"]
        object.__setattr__(server, "allowed_tools", ())
        pool = _FakePool()
        executor = MCPExternalOperationExecutor(
            registry=registry,
            pool=pool,
            ledger=ExternalExecutionLedger(path=self.temp_dir / "client.sqlite3"),
        )
        receipt = executor.execute(_email_request())
        self.assertEqual(
            receipt.error_code,
            ExternalExecutionErrorCode.TOOL_NOT_ALLOWLISTED,
        )
        self.assertEqual(pool.calls, [])


class MCPExternalExecutorIntegrationTests(unittest.TestCase):
    # Create one controlled directory for local SMTP, SQLite, and MCP ledgers.
    def setUp(self) -> None:
        temp_root = REPO_ROOT / ".tmp"
        temp_root.mkdir(exist_ok=True)
        self.temp_dir = temp_root / f"mcp-integration-{uuid4()}"
        self.temp_dir.mkdir()

    # Remove only temporary controlled integration resources.
    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # Invoke the real FastMCP database server against an isolated SQLite database.
    def test_real_mcp_database_execution_and_session_reuse(self) -> None:
        database_path = self.temp_dir / "dedicated.sqlite3"
        connection = sqlite3.connect(database_path)
        connection.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, label TEXT)")
        connection.close()
        profiles_path = self.temp_dir / "profiles.json"
        profiles_path.write_text(
            json.dumps(
                {
                    "profiles": {
                        "integration.write": {
                            "driver": "sqlite",
                            "dsn_env": "LAYER2_TEST_DATABASE_DSN",
                            "read_only": False,
                            "timeout_seconds": 3,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        registry = _enabled_registry(
            self.temp_dir,
            capability_id="database.statement.execute",
            extra_environment_keys=("LAYER2_TEST_DATABASE_DSN",),
        )
        environment = {
            "LAYER2_DATABASE_PROFILES_CONFIG_PATH": str(profiles_path),
            "LAYER2_TEST_DATABASE_DSN": str(database_path),
            "LAYER2_EXTERNAL_SERVER_LEDGER_PATH": str(
                self.temp_dir / "database-server-ledger.sqlite3"
            ),
        }
        pool = MCPConnectionPool(registry=registry)
        executor = MCPExternalOperationExecutor(
            registry=registry,
            pool=pool,
            ledger=ExternalExecutionLedger(
                path=self.temp_dir / "database-client-ledger.sqlite3"
            ),
        )
        try:
            with patch.dict(os.environ, environment, clear=False):
                receipts = []
                for index in (1, 2):
                    proposal = normalize_sql_operation(
                        operation_id=f"database-operation-{index}",
                        plan_step_id="database-step",
                        capability_id="database.statement.execute",
                        sql="INSERT INTO events(label) VALUES (:label)",
                        database_target="integration.write",
                        database_profile_id="integration.write",
                        parameters=(
                            DatabaseOperationParameter(
                                name="label",
                                value=f"event-{index}",
                            ),
                        ),
                        execution_candidate=True,
                    )
                    request = build_external_execution_request(
                        proposal=proposal,
                        authorized_payload_digest=proposal.payload_digest,
                        execution_intent=ExecutionIntent.EXECUTE_AFTER_GOVERNANCE,
                        authorization_source=ExternalExecutionAuthorizationSource.HUMAN_REVIEW,
                    )
                    receipts.append(executor.execute(request))
            self.assertTrue(
                all(
                    receipt.status == ExternalExecutionStatus.EXECUTED
                    for receipt in receipts
                )
            )
            self.assertEqual(len(pool._sessions), 1)
            connection = sqlite3.connect(database_path)
            rows = connection.execute(
                "SELECT label FROM events ORDER BY id"
            ).fetchall()
            connection.close()
            self.assertEqual(rows, [("event-1",), ("event-2",)])
        finally:
            pool.close()

    # Send through the real FastMCP email server into a local capture transport only.
    def test_real_mcp_email_uses_local_capture_server(self) -> None:
        capture = _CaptureSMTPServer(("127.0.0.1", 0))
        thread = threading.Thread(target=capture.serve_forever, daemon=True)
        thread.start()
        registry = _enabled_registry(
            self.temp_dir,
            capability_id="communication.email.send",
        )
        environment = {
            "LAYER2_SMTP_HOST": "127.0.0.1",
            "LAYER2_SMTP_PORT": str(capture.server_address[1]),
            # Override any developer SMTP credentials loaded from the root .env.
            "LAYER2_SMTP_USERNAME": "",
            "LAYER2_SMTP_PASSWORD": "",
            "LAYER2_SMTP_SENDER": "sender@example.test",
            "LAYER2_SMTP_USE_TLS": "false",
            "LAYER2_SMTP_USE_SSL": "false",
            "LAYER2_SMTP_TIMEOUT_SECONDS": "3",
            "LAYER2_EXTERNAL_SERVER_LEDGER_PATH": str(
                self.temp_dir / "email-server-ledger.sqlite3"
            ),
        }
        executor = MCPExternalOperationExecutor(
            registry=registry,
            ledger=ExternalExecutionLedger(
                path=self.temp_dir / "email-client-ledger.sqlite3"
            ),
        )
        try:
            with patch.dict(os.environ, environment, clear=False):
                receipt = executor.execute(
                    _email_request(f"email-operation-{uuid4()}")
                )
            self.assertEqual(receipt.status, ExternalExecutionStatus.EXECUTED)
            self.assertTrue(receipt.external_reference)
            self.assertEqual(len(capture.messages), 1)
            self.assertIn(b"Captured integration", capture.messages[0])
        finally:
            executor.close()
            capture.shutdown()
            capture.server_close()
            thread.join(timeout=3)

    # Exercise real transport timeout, allowlist enforcement, and explicit closure.
    def test_real_pool_timeout_allowlist_and_close(self) -> None:
        config = {
            "registry_version": "external-timeout-test.v1",
            "capabilities": [],
            "mcp_servers": {
                "slow": {
                    "enabled": True,
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [
                        "-m",
                        "tests.fixtures.fastmcp_external_test_server",
                    ],
                    "allowed_tools": ["slow_operation"],
                    "timeout_seconds": 5,
                    "environment_keys": [],
                    "working_directory": str(REPO_ROOT),
                }
            },
        }
        config_path = self.temp_dir / "slow-registry.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        registry = load_external_capability_registry(config_path=config_path)
        pool = MCPConnectionPool(registry=registry)
        try:
            with self.assertRaises(MCPCallTimeout):
                pool.call_tool(
                    server_id="slow",
                    tool_name="slow_operation",
                    arguments={"delay_seconds": 2.0},
                    timeout_seconds=0.1,
                )
            with self.assertRaisesRegex(Exception, "not allowlisted"):
                pool.call_tool(
                    server_id="slow",
                    tool_name="unknown",
                    arguments={},
                    timeout_seconds=0.1,
                )
        finally:
            pool.close()
        with self.assertRaises(MCPPoolClosedError):
            pool.call_tool(
                server_id="slow",
                tool_name="slow_operation",
                arguments={"delay_seconds": 0.0},
                timeout_seconds=0.1,
            )

    # Test full end-to-end runtime graph execution: graph -> governance -> real stdio MCP server -> SQLite SELECT receipt.
    def test_graph_e2e_real_mcp_database_execution(self) -> None:
        db_file = self.temp_dir / "test_e2e.db"
        conn = sqlite3.connect(db_file)
        conn.execute("CREATE TABLE users (id INT, name TEXT, role TEXT)")
        conn.execute("INSERT INTO users VALUES (1, 'Alice', 'admin')")
        conn.commit()
        conn.close()

        profiles_file = self.temp_dir / "db_profiles.json"
        profiles_file.write_text(
            json.dumps(
                {
                    "profiles": {
                        "enterprise_db.read_only": {
                            "driver": "sqlite",
                            "dsn_env": "LAYER2_TEST_E2E_DB_DSN",
                            "read_only": True,
                            "timeout_seconds": 10,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        registry = _enabled_registry(
            self.temp_dir,
            capability_id="database.statement.execute",
            extra_environment_keys=("LAYER2_TEST_E2E_DB_DSN",),
        )

        from layer2_runtime_governance.composition import (
            Layer2RuntimeDependencies,
            compose_layer2_runtime,
        )
        from layer2_runtime_governance.domain import (
            ActorResult,
            ActorResultType,
            PlanStep,
            PlanStepStatus,
            PlannerDecision,
            RuntimeState,
            TaskState,
        )
        from tests.layer2_runtime_fixtures import StubPolicyEngine
        from layer2_runtime_governance.actor_contract import ActorPort, ActorTask
        from layer2_runtime_governance.judge import DisabledJudge
        from layer2_runtime_governance.planning import PlannerPort

        class _FixedPlanner(PlannerPort):
            planner_name = "fixed_planner"
            prompt_version = "v1"
            def plan(self, *, task_state: TaskState) -> PlannerDecision:
                return PlannerDecision(
                    plan_steps=[
                        PlanStep(
                            step_id="step_1",
                            objective="Query admin user.",
                            rationale="Check admin user existence.",
                            expected_outcome="User data.",
                            status=PlanStepStatus.PENDING,
                        )
                    ],
                    rationale="Query admin user.",
                    planner_name=self.planner_name,
                    prompt_version=self.prompt_version,
                )

        from layer2_runtime_governance.domain import StepValidation

        class _SingleTurnDbActor(ActorPort):
            actor_name = "db_actor"
            prompt_version = "v1"
            def act(self, *, actor_task: ActorTask) -> ActorResult:
                proposal = normalize_sql_operation(
                    operation_id="db-op-1",
                    plan_step_id=actor_task.current_plan_step.step_id,
                    capability_id="database.statement.execute",
                    sql="SELECT id, name, role FROM users WHERE role = 'admin'",
                    database_target="enterprise_db",
                    database_profile_id="enterprise_db.read_only",
                )
                return ActorResult(
                    plan_step_id=actor_task.current_plan_step.step_id,
                    result_type=ActorResultType.STEP_COMPLETED,
                    summary="Query admin user via SQL",
                    external_operation_proposal=proposal,
                    external_impact=True,
                    step_validation=StepValidation(
                        plan_step_id=actor_task.current_plan_step.step_id,
                        actor_self_confidence=0.95,
                        evidence_refs=tuple(
                            item.observation_id for item in actor_task.observations
                        ),
                        rationale_summary="Query executed.",
                    ),
                )

        from layer2_runtime_governance.judge import (
            JudgeAssessment,
            JudgeAssessmentStatus,
            JudgePort,
            JudgeRecommendedDirective,
            JudgeSeverity,
            JudgeVerdict,
        )

        class _SafeJudge(JudgePort):
            judge_name = "safe_judge"
            prompt_version = "v1"
            def evaluate(self, *, context):
                return JudgeAssessment(
                    invoked=True,
                    recommendation=JudgeRecommendedDirective.CONTINUE,
                    confidence=0.95,
                    summary="Safe DB query.",
                    rationale="Safe DB query.",
                    flags=(),
                    evaluated_target=context.external_operation.operation_id,
                    status=JudgeAssessmentStatus.ACTIVE,
                    verdict=JudgeVerdict.SAFE,
                    severity=JudgeSeverity.LOW,
                    evaluated_operation_digest=context.external_operation.payload_digest,
                    evaluated_evidence_snapshot_digest=context.evidence_snapshot_digest,
                    evaluated_evidence_selection_digest=context.evidence_selection_digest,
                    prompt_version=self.prompt_version,
                )

        executor = MCPExternalOperationExecutor(
            registry=registry,
            ledger=ExternalExecutionLedger(path=self.temp_dir / "e2e-ledger.sqlite3"),
        )

        from layer1_intake import (
            BudgetConfig,
            ExecutionPolicy,
            IntakeMetadata,
            IntakeResult,
            ParseStatus,
            StructuredTask,
            TaskSpec,
        )

        intake_res = IntakeResult(
            structured_task=StructuredTask(
                task_spec=TaskSpec(
                    goal="Query db",
                    execution_intent=ExecutionIntent.EXECUTE_AFTER_GOVERNANCE,
                ),
                execution_policy=ExecutionPolicy(
                    read_only_mode=False,
                    confidence_threshold=0.5,
                    budget=BudgetConfig(max_steps=5, max_tool_calls=5),
                ),
            ),
            metadata=IntakeMetadata(
                request_id="e2e-1",
                parse_status=ParseStatus.COMPLETE,
            ),
        )

        env_updates = {
            "LAYER2_DATABASE_PROFILES_CONFIG_PATH": str(profiles_file),
            "LAYER2_TEST_E2E_DB_DSN": str(db_file),
            "PYTHONPATH": str(SRC_ROOT),
        }

        try:
            with patch.dict(os.environ, env_updates, clear=False):
                runtime_graph = compose_layer2_runtime(
                    Layer2RuntimeDependencies(
                        planner=_FixedPlanner(),
                        actor=_SingleTurnDbActor(),
                        policy_engine=StubPolicyEngine(),
                        external_capability_registry=registry,
                        external_operation_executor=executor,
                        judge=_SafeJudge(),
                    )
                )
                res = runtime_graph.invoke(
                    {"intake_result": intake_res},
                    config={"configurable": {"thread_id": "test_e2e_thread"}},
                )
                final_state = res["task_state"]

                self.assertEqual(final_state.current_state, RuntimeState.COMPLETED)
                self.assertTrue(len(final_state.external_execution_history) > 0)
                receipt = final_state.external_execution_history[-1]
                self.assertEqual(receipt.status, ExternalExecutionStatus.EXECUTED)
                meta = {field.name: field.value for field in receipt.connector_metadata}
                self.assertEqual(meta.get("row_count"), "1")
                self.assertIn("Alice", meta.get("rows", ""))
        finally:
            executor.close()


if __name__ == "__main__":
    unittest.main()
