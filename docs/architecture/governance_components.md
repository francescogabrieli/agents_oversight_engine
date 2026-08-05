# Component architecture: auditable oversight system

The associated diagram, [`governance-components.mmd`](governance-components.mmd), is a component-level view of the implemented oversight system. It intentionally contains 15 component nodes: enough to make the enforcement, policy, HITL, observability, and external-execution boundaries explicit without reproducing the LangGraph node graph.

## Reading guide

- Solid arrows are the live execution/data path: intake, planning/acting, governed tools, and the capability-bound external path.
- Dashed arrows are control, policy, or persistence interactions: PDP queries/decisions, HITL interruption/resume, checkpointing, and trace projections.
- Group frames label trust boundaries. The local application process is distinct from the OPA PDP, the HITL control plane, and external systems.
- `External execution` is implemented but capability-bound and disabled by the default registry. The diagram therefore shows the architectural boundary, not an always-active side effect.
- `Risk` and `Behavioral` appear inside pillar signal services, but not as decision authorities: the OPA bundle marks both shadow-only. `Evidence`, `Trajectory`, and the governed-external Judge are active inputs to the PDP decision.

## Component evidence

| Diagram component | Implemented responsibility | Source evidence |
|---|---|---|
| `IntakeService` | Builds the canonical Layer 1 `IntakeResult`. | [`src/layer1_intake/service.py`](../../src/layer1_intake/service.py) — `IntakeService.execute` |
| `build_runtime_graph` | Creates the LangGraph nodes and the conditional routing. | [`src/layer2_runtime_governance/graph.py`](../../src/layer2_runtime_governance/graph.py) — `build_runtime_graph` |
| `planner` and `actor` | Execute the semantic planner/actor split through node adapters. | [`src/layer2_runtime_governance/runtime/node_adapters.py`](../../src/layer2_runtime_governance/runtime/node_adapters.py) — `build_planner_node`, `build_actor_node`; [`runtime/actor_service.py`](../../src/layer2_runtime_governance/runtime/actor_service.py) — `actor` |
| `governance` | Runs OPA-first evaluation, persists assessment history, and applies the validated directive. | [`src/layer2_runtime_governance/runtime/governance_service.py`](../../src/layer2_runtime_governance/runtime/governance_service.py) — `governance`, `_apply_governance_decision` |
| Pillar signal services | Build typed Evidence, Trajectory, Risk, Behavioral, and Judge inputs. | [`src/layer2_runtime_governance/signal_services`](../../src/layer2_runtime_governance/signal_services) — `build_evidence_signal`, `build_trajectory_signal`, `build_risk_signal`, `build_behavioral_signal`, `build_judge_signal` |
| `OPA/Rego PDP` | Selects required pillars and returns the sole final directive. | [`src/layer2_runtime_governance/governance_components/policy_engine.py`](../../src/layer2_runtime_governance/governance_components/policy_engine.py) — `OPAHttpPolicyEngine`; [`configs/opa/layer2_governance/main.rego`](../../configs/opa/layer2_governance/main.rego) — `required_pillars`, `decision` |
| `OpenAIToolGateway` | Validates and dispatches typed read-only tool requests. | [`src/layer3_tool_bus_external_capabilities/tools/gateway.py`](../../src/layer3_tool_bus_external_capabilities/tools/gateway.py) — `OpenAIToolGateway.execute`; [`configs/layer2_tool_gateway.json`](../../configs/layer2_tool_gateway.json) |
| `external_execution` | Checks digest, authorization, registry, and receipt invariants before connector invocation. | [`src/layer2_runtime_governance/runtime/external_execution_service.py`](../../src/layer2_runtime_governance/runtime/external_execution_service.py) — `external_execution`, `_preflight_external_execution` |
| Configured MCP capability boundary | Maps an enabled external capability to one allowlisted MCP tool; the supplied email/database entries are disabled by default. | [`src/layer3_tool_bus_external_capabilities/external_operations/registry.py`](../../src/layer3_tool_bus_external_capabilities/external_operations/registry.py) — `ExternalCapabilityRegistry`, `_validate_registry`; [`configs/layer2_external_capabilities.json`](../../configs/layer2_external_capabilities.json) |
| HITL gateway and dashboard | Uses LangGraph interrupt/resume plus a request-bound durable decision inbox. | [`src/layer2_runtime_governance/hitl/__init__.py`](../../src/layer2_runtime_governance/hitl/__init__.py) — `human_review`, `build_human_review_request`; [`hitl/decision_inbox.py`](../../src/layer2_runtime_governance/hitl/decision_inbox.py) — `ReviewDecisionInboxPort`; [`src/main_layer2_dashboard.py`](../../src/main_layer2_dashboard.py) |
| Checkpointer and artifacts | Persists authoritative LangGraph checkpoints separately from trace/dashboard artifacts. | [`src/layer4_state_evidence_observability/infrastructure/checkpointing.py`](../../src/layer4_state_evidence_observability/infrastructure/checkpointing.py) — `build_default_checkpointer`; [`infrastructure/runtime_artifacts.py`](../../src/layer4_state_evidence_observability/infrastructure/runtime_artifacts.py) — `RuntimeArtifactStore` |

## Scope and deliberate omissions

This diagram does not show individual LangGraph nodes, the complete `TaskState` object graph, the Rego directive precedence, concrete provider prompts, or the internal SQLite schema. Those are separate technical views; showing them here would obscure the thesis-level argument: the PEP supervises an operational agent, OPA alone decides the directive, human intervention is typed and request-bound, and traceable artifacts preserve accountability.

For the full implementation reconstruction, see [`../generated/architecture-analysis.md`](../generated/architecture-analysis.md).
