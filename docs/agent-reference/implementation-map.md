# Implementation map and active baseline

This document is a required reference under [AGENTS.md](../../AGENTS.md).
Read it when the routing table there applies. It preserves the detailed sections
extracted from that file; these are maintained instructions, not a historical archive.
All code paths and commands below are relative to the repository root.

## Current Scope

At the moment, the repository covers five numbered architectural layers; Layer 5 currently implements the offline Evaluation slice, while Continuous Improvement remains deferred:

- `Layer 1 - Task Intake`: canonical task parsing, completeness assessment, and execution-policy assignment
- `Layer 2 - Runtime & Governance`: bootstrap, planner/actor loop, governance, HITL routing, and coordination
- `Layer 3 - Tool Bus / External Capabilities`: governed capability execution and external-operation boundaries
- `Layer 4 - State, Evidence & Observability`: state projections, evidence, oversight, checkpoints, traces, and artifacts
- `Layer 5 - Evaluation & Continuous Improvement`: offline trace ingestion, evaluation storage, metrics, annotations, plots, and reports; feedback loop deferred

Layer 2 is the live orchestration and governance slice, not the whole application. It coordinates Layer 1 input and consumes typed services from Layer 3 and Layer 4; capability execution and durable observability remain owned by those layers.
It already includes:

- typed planner output
- typed actor output
- runtime-bound read-only tool requests
- tool result observation into explicit evidence
- audit-safe scratchpad entries
- typed step self-validations
- an active `replanner` branch for `NEEDS_REPLAN`
- typed Python raw-fact collection for active evidence/trajectory and shadow operational consequence assessment
- explicit separation of static policy, dynamic policy, behavioural foundation, judge signals, and OPA-owned final decision policy
- local SQLite behavioural memory as a non-authoritative read model for historical run features
- behavioural similarity, deviation, and novelty scoring as a shadow-only SQLite read model
- mandatory typed semantic judge assessments for non-denied governed-external execution candidates
- versioned Rego precedence with static/evidence/trajectory/judge active and operational risk/behavioral shadow-only
- Minimal Safe Intervention expressed in the OPA bundle, with explicit trace visibility and preserved read-only constraints
- typed human-review requests and decisions with LangGraph `interrupt / resume`
- durable local LangGraph checkpoint persistence and per-thread runtime artifacts on disk
- typed `external_communication` phase tracking for evidence, draft, review/edit, approval, and blocked states
- the external communication draft body is taken from the latest governed draft output for the branch, while the trace retains the canonical final body, selected evidence references, and provenance diagnostics
- external communication plan semantics remain planner-owned; runtime compaction is not part of the active architecture
- typed runtime trace export through `src/layer4_state_evidence_observability/observability/`, surfaced by `src/main_layer2.py`
- typed termination classification (`termination_reason`) for governance denial, human abort, external execution failure, and runtime failure, exported in the trace and runtime summary
- typed retrieval provenance (`search_snippet`, `fetched_document`, controlled-resource, MCP, or generic capability result) carried from Layer 3 through Evidence, checkpoints, and trace export
- deterministic TaskSpec evidence floors that raise underdeclared planner/replanner burdens without reading governance thresholds from Python
- shared evidence coverage evaluation (`evaluate_actor_evidence_coverage`) providing canonical origin counting, gate enforcement, and explicit actor guidance (`evidence_coverage` payload with unfetched candidate locators)
- an explicit `unresolvable_revision` trajectory fact/reason for repeated judge `needs_revision` without a new observational basis; it remains a non-authorizing HITL boundary

These are the folders to treat as the active implementation baseline:

- `src/layer1_intake`
- `src/layer2_runtime_governance`
- `src/layer3_tool_bus_external_capabilities`
- `src/layer4_state_evidence_observability`
- `src/layer5_evaluation_continuous_improvement`
- `src/main_layer1.py`
- `src/main_layer2.py`
- `src/main_layer2_dashboard.py`
- `src/governance_policy_service`
- `services/policy_decision_point`

Supporting material lives in:

- `configs/`
- `docs/`
- `frontend/dashboard`
- `tests/`


## Architectural Intent

The repository follows the architecture described in `docs/architecture/architecturev3.md`.
It should also remain aligned with the objectives and design priorities described in
`training_plan.md`. The repository uses the following contribution boundary:

- **supervised system:** an autonomous agent that plans, acts, uses tools, observes
  results, and progresses without collapsing into a single wrapped LLM call;
- **central thesis contribution:** the oversight, governance, and HITL engine that
  determines when autonomy may continue and when correction, escalation, approval,
  reconciliation, or termination is required;
- **accountability substrate:** typed state, evidence, decisions, checkpoints, trace,
  and digest-bound artifacts that make interventions reconstructible;
- **evaluation objective:** measure not only task effectiveness but also escalation
  quality, human-intervention rate, feedback quality, policy compliance, and behavior
  under uncertainty.

Autonomy must therefore remain operationally credible, but new work should not present
agent sophistication as the sole or primary research contribution. The distinctive
claim to be evaluated is the effectiveness and auditability of the oversight design.

High-level macro-blocks:

1. `Task Intake`
2. `Runtime & Governance`
3. `Tool Bus / External Capabilities`
4. `State, Evidence & Observability`
5. `Evaluation & Continuous Improvement`

Numbered layer ownership:

- Layer 1 owns intake and canonical task contracts.
- Layer 2 owns live orchestration, governance, HITL routing, and enforcement.
- Layer 3 owns the Tool Bus, external capabilities, normalization, and connector boundaries.
- Layer 4 owns state, evidence, oversight, persistence, trace, and observability.
- Evaluation is a separate offline block, not a fifth runtime layer.

Important implementation principles:

- `Layer 1` is external to the LangGraph runtime and produces the canonical intake contract.
- `Layer 2` is the live LangGraph orchestration and governance layer; it owns routing and enforcement, but not Layer 3 connector logic or Layer 4 persistence internals.
- `Layer 3` is the Tool Bus / External Capabilities boundary; Layer 2 requests capabilities through typed gateways, while actors and governance never call connectors directly.
- `Layer 4` is the State, Evidence & Observability boundary; it owns typed evidence, trace, checkpoint, and artifact projections.
- Layer 5 Evaluation consumes exported `layer2_trace.v6` artifacts offline through its own SQLite projection and never enters the live runtime loop; Continuous Improvement is not yet implemented.
- for the `planner + actor` branch, follow `docs/guidelines/plan-and-execute.ipynb` as the primary implementation pattern reference:
  - multi-step semantic planner
  - step-bounded executor
  - structured tool binding at the runtime level
- `HITL` is implemented through `interrupt / resume` and should remain typed and auditable.
- `external_communication` is handled conservatively:
  - first evidence collection
  - then draft proposal
  - then human review, with typed edit-and-submit support on the governed draft
  - then the approved resume path remains the only path toward external execution
- policy and governance logic should remain explicit and explainable


## Repository Map

### `src/layer1_intake`

Purpose:

- parse raw task input into a canonical structured payload
- assign execution policy
- attach intake metadata

Core outputs:

- `TaskSpec`
- `ExecutionPolicy`
- `IntakeMetadata`
- `IntakeResult`

Notes:

- current parser implementation uses OpenAI for real parsing
- execution policy is resolved from `configs/layer1_execution_policies.json`
- `context` missing is a warning, not a mandatory downgrade to `partial`
- Pydantic owns Layer 1 shape, type, and whitespace normalization; semantic completeness is resolved once by the pure `assess_task_completeness` function into `TaskCompletenessAssessment`
- `TaskSpecBuilder` orchestrates parsing and completeness assessment, while `IntakeResultValidator` verifies only the final canonical invariants


### `src/layer2_runtime_governance`

Purpose:

- initialize runtime state from Layer 1
- produce the first semantic planner snapshot
- execute the current semantic plan step through the actor
- execute read-only capability calls through `tool_gateway`
- observe tool results and feed them back into the actor loop
- replace the current and pending semantic trajectory through `replanner` when the actor signals `NEEDS_REPLAN`
- evaluate actor outcomes through a typed governance node backed by an OPA policy engine
- send typed raw facts and advisory context to OPA, which alone scores and returns the final directive
- invoke a typed semantic judge only when OPA requires it for an exact governed external proposal and digest
- persist governance decisions in `TaskState` with an explicit `ABORTED` stop state for hard denials
- persist oversight assessments in `TaskState` for audit and thesis traceability
- maintain an audit-safe scratchpad and typed step self-validations during actor execution
- stop after the current planner/actor/tool slice reaches a stable stop point in canonical `TaskState`

Main files:

- `state.py`
- `graph.py`
- `planning/`
- `execution/factory.py`
- `execution/providers/openai.py` (default actor for real runs)
- `execution/providers/bedrock.py` (historical comparison adapter)
- `replanning.py` (compatibility facade)
- `actor_contract.py`
- `src/layer3_tool_bus_external_capabilities/tool_contract.py`
- `src/layer3_tool_bus_external_capabilities/tools/gateway.py`
- `governance.py`
- `src/layer4_state_evidence_observability/behavioral/`
- `judge/`
- `src/layer4_state_evidence_observability/oversight/`
- `execution/providers/bedrock_runtime.py` (historical comparison adapter)
- `src/layer4_state_evidence_observability/infrastructure/runtime_artifacts.py`
- `runtime_warnings.py`

Modular capability packages introduced for the active Layer 2–4 baseline:

- `domain/` exposes framework-independent views of canonical planning, execution, evidence, review, and task-state contracts
- `runtime/` owns graph contracts, routing, node adapters, and thread context
- `src/layer3_tool_bus_external_capabilities/tools/` owns the Tool Gateway registry, validation, backend adapters, and content extractors
- `execution/` owns the actor factory, provider configuration, prompts, action schemas, response parsing, result construction, and provider adapters
- `governance_components/` owns policy contracts, inputs, adapters, OPA-first evaluation, response parsing, and fail-closed handling
- `src/layer4_state_evidence_observability/observability/` owns immutable trace models, read-only builders, compact views, and JSON serialization
- `src/layer5_evaluation_continuous_improvement/` owns offline v6 trace ingestion, the dedicated SQLite evaluation projection, annotations, metrics, and dependency-free SVG/CSV/JSON reporting
- `scripts/run_evaluation_campaign.py` preregisters the entire campaign and synchronizes its records/traces into the evaluation database before and after each CLI run; failed, interrupted, skipped, and not-started runs remain distinguishable. Undefined evaluation rates remain null/N/D rather than becoming zero in reports and thesis plots.
- `notebooks/thesis_plotting.py` is a companion matplotlib/seaborn renderer for LaTeX-includable PDF/PNG (300dpi) figures; it is presentation-only and must read every rate/count from `layer5_evaluation_continuous_improvement.metrics.build_evaluation_summary` rather than recomputing metrics from raw tables, and must never fabricate sample data - its dependencies live in the separate `requirements-evaluation.txt`
- `src/layer4_state_evidence_observability/infrastructure/` owns checkpoint composition and persistence ports
- `composition.py` is the explicit dependency-injection entry point for runtime construction

Current design:

- `domain/` owns the canonical bootstrap state, runtime flags, plan models, actor execution records, review contracts, `TaskState`, and serialization recovery; `state.py` is an import-compatible facade
- `domain/plan.py` carries only semantic `PlanStep` fields: identifier, objective, rationale, expected outcome, and runtime-owned status
- `runtime/` owns lifecycle, planner, actor, governance, tool observation, replanning services, and thin LangGraph node adapters; the retired `nodes.py` facade must not be restored
- `planning/` defines the planner port, prompt builders, the default OpenAI-backed planner, and replanning adapters
- `execution/factory.py` builds the configured actor; real runs default to the OpenAI Responses API adapter with `gpt-6-luna` and the existing `OPENAI_API_KEY`. Bedrock/OpenAI adapters, prompts, action schemas, response parsing, and result construction live under `execution/`
- `actor_contract.py` passes observed concrete evidence and the semantic current step; it never passes a universal evidence rank or claim ceiling
- `evaluate_actor_evidence_coverage` in `signal_services/evidence_service.py` is the shared canonical owner for evidence coverage evaluation, powering both the action schema completion gate (`_evidence_burden_unsatisfied`) and the actor prompt payload (`evidence_coverage`), which exposes satisfied origins, still-needed counts, and unfetched candidate locators from search snippets to avoid redundant fetches
- Evidence evaluation treats a typed, parser-valid single `SELECT` database proposal as an acquisition operation rather than a claim about rows not retrieved yet; the planner-declared factual marker remains exported separately for audit, while static SQL policy, digest binding, HITL authorization, and post-execution accountability remain unchanged
- `planning/replanning.py` defines the replanner port, prompt builders, the default OpenAI-backed replanner, and a local conservative fallback replanner for tests/offline use; `replanning.py` preserves the legacy import path
- `actor_contract.py` defines the typed handoff from planner state into actor-facing execution context for the current plan step, including observed evidence, scratchpad entries, and prior step validations
- `src/layer3_tool_bus_external_capabilities/tool_contract.py` defines typed tool requests, tool results, observed evidence contracts, and the canonical operational identity derived from capability plus normalized executable arguments; actor rationale is excluded
- `src/layer3_tool_bus_external_capabilities/tools/` contains the read-only operational tool surface currently available to the runtime, including `web_search`, `fetch_url_text`, and configured read-only MCP test tools behind the gateway registry
- `configs/layer2_tool_gateway.json` is the active Tool Gateway registry for configured native, HTTP, OpenAI, and MCP-backed tools
- MCP is usable as a governed backend behind the Tool Gateway; the actor must never call MCP servers directly
- `governance_components/` defines typed policy contracts, OPA inputs and adapter, OPA-first orchestration, decision parsing, fail-closed handling, and final governance traces; `governance.py` retains checkpoint aliases only
- `governance_policy_service` owns the runtime-independent HTTP transport contract for the external OPA Policy Decision Point; it must not import Layer 2 runtime state
- `services/policy_decision_point` is the deployable, stateless OPA service boundary; LangGraph remains the Policy Enforcement Point and applies only validated typed directives. The local compose runs OPA with `--watch`, which can silently fail to reload on a Windows bind mount: the process keeps running and `policy_bundle_version` never changes, so a version-string check alone cannot detect it. Official runs verify the loaded bundle with `scripts/verify_opa_bundle.py`, which compares the exact module source OPA reports via `GET /v1/policies` and the `policy_config` document via `GET /v1/data/policy_config` against the files on disk under `configs/opa/layer2_governance` — content equality, not a maintained version string — before starting or resuming a fresh thread
- OPA/Rego is the sole final directive authority; Python may normalize and validate transport data but must not recreate a competing final decision combiner
- `src/layer4_state_evidence_observability/behavioral/` defines the shadow-only SQLite historical read model, similarity/deviation/novelty scoring, persistence, and counterfactual recommendations with no directive influence
- `judge/` defines the generic governed-external semantic assessor; OPA owns invocation and directive mapping
- `src/layer4_state_evidence_observability/oversight/contracts.py` retains typed assessment/checkpoint contracts; `signal_services/` produces raw facts and OPA owns scoring, thresholds, judge requirements, and directives
- `src/layer4_state_evidence_observability/oversight/` preserves persisted oversight assessment contracts; evidence and trajectory are active, while `operational_risk` exports shadow consequence facts and is not applicable outside governed external operations
- `src/layer3_tool_bus_external_capabilities/external_operations/` owns immutable typed proposals, execution intent projection, SQL AST and communication/generic normalizers, a dedicated capability registry, durable idempotency records, the external executor port, and the capability-bound MCP adapter
- `src/layer3_tool_bus_external_capabilities/external_capability_mcp/` owns the productive typed MCP tools for SMTP email and profile-bound database statements; both are disabled by default and credentials remain infrastructure configuration
- `execution/providers/openai.py` defines the default Layer 2 actor adapter and exposes its configured model ID for trace provenance. The Bedrock Converse adapter and its provider-specific reasoning envelopes remain available for historical comparison runs, but are not the standard live path.
- `runtime/governance_service.py` applies the OPA-first governance result and persists static, dynamic, behavioral, and judge records before routing
- `src/layer4_state_evidence_observability/infrastructure/runtime_artifacts.py` configures the local official LangGraph SQLite saver plus the per-thread runtime artifact store; `src/layer4_state_evidence_observability/infrastructure/checkpointing.py` can select the official PostgreSQL saver for deployed environments
- `src/layer4_state_evidence_observability/observability/` defines the typed, JSON-exportable Layer 4 trace projections for Layer 2 runs, builders, and compact views used for debugging, thesis evidence, HITL payloads, and future evaluation; `trace_summary.py` produces a human-readable Markdown summary written alongside the full JSON trace as `trace_summary.md`; `governance_narrative.py` produces `governance_narrative.md`, a turn-by-turn account of what the actor and governance said to each other, built from the exported trace (so it can also be rendered for already-archived runs) rather than from the live log stream - it collapses routine passes into one line, quotes the actor's own recorded rationale, names the exact flagged values and the operator's decision, and never replaces `governance_exchange.md`, which stays the complete authoritative audit stream; the retired `trace.py` facade must not be restored
- `schemas/layer2_trace.schema.json` is the closed Draft 2020-12 contract for runtime-produced `layer2_trace.v6`; the current Layer 5 importer accepts this version only and does not silently migrate legacy traces or exchange-only runs. Retrieval-kind, evidence-burden, error-code, and reason-code additions are explicit v6 contract fields; a future incompatible trace change must bump the version.
- Fields added to `v6` after it shipped (`toolObservation.retrieval_kind`, `evidenceBurden.insufficiency_declaration_permitted`) are declared but **not** `required`. Absence means the producing runtime predates the field, never that the value is false or unknown-and-substitutable. Keeping them optional is what makes the addition genuinely backward compatible: artifacts already exported as `layer2_trace.v6` continue to validate, so one version label never covers two mutually invalid shapes. Making such a field `required` is an incompatible change and must bump the trace version together with the Layer 5 importer. `tests/test_layer2_trace.py` pins this rule.
- `hitl/dashboard.py` defines the dashboard-facing snapshot model derived from `TaskState`, governance, oversight, and HITL review payloads, including bounded Evidence/Trajectory diagnostics, judge findings, and selected persisted evidence references for reviews that have no external proposal
- `main_layer2_dashboard.py` acts as a standalone multi-thread HITL control plane backend that serves the React dashboard build and exposes review inbox, detail, and typed decision-submission APIs; it validates and durably enqueues decisions but never executes or resumes the LangGraph runtime
- `src/layer4_state_evidence_observability/infrastructure/review_decision_inbox.py` is the durable control-plane transport between the dashboard and the runtime owner; it provides idempotent request-bound submissions, atomic claiming, delivery status, and a single-owner lease, but it is not runtime state and cannot reconstruct a graph
- the original `main_layer2.py` process remains the execution owner while a thread is interrupted, consumes the queued decision, and applies `Command(resume=...)` through the official checkpointer. Both initial execution and resume stream canonical node updates even when terminal progress is disabled. `RuntimeArtifactStore.persist_progress` refreshes only dashboard/summary projections per node; `persist` reuses the same projection serializer and also exports full traces, narratives and Judge bundles at stable boundaries.
- the dashboard removes a submitted request from active approvals immediately, exposes it as a non-interactive completed-review item for 2 minutes, and then removes that transient acknowledgement; canonical run completion remains distinct from review completion
- `frontend/dashboard` contains the standalone React + TypeScript + Tailwind + daisyUI dashboard frontend that is built into static assets and served by `main_layer2_dashboard.py`
- Dashboard review forms preserve local edits across equivalent polls, reset for a changed request/candidate, and require explicit decision selection. Proposal details are displayed before approval; email edits preserve the remaining typed draft fields. A sticky navigation link keeps decisions reachable, source links permit only HTTP(S), and bounded excerpts are labelled as partial. Submission delivery is distinct from run completion; the UI never claims the runtime is alive from a static badge. These are presentation safeguards, not an alternative authorization policy.
- `hitl/dashboard.py` projects the pending candidate's complete `actor_output_text` (falling back to the current actor output only without a candidate). Selected observations preserve paragraphs within a 12,000-character limit and carry optional `content_truncated`, `observation_available`, and `retrieval_kind` metadata. Missing selected IDs remain visible instead of disappearing. These are additive dashboard DTO fields, not changes to `layer2_trace.v6`. `EvidenceComparisonPanel` owns the source selector and comparison; `FlaggedEvidencePanel` owns recorded literal passages and explicit missing-context fallbacks. Historical snapshots are not rewritten or reconstructed; an available governed draft is labelled as a proposal when used in the comparison. Reconciliation receipts precede decision controls, and clarification labels describe recording an answer and resuming the reviewed step.
- `pillarSummary.ts` distinguishes actor-selected independent origins from the retrieval universe and whole-run replan totals from episode counts. The dashboard projects current-step tool attempts and OPA's exhaustion flag without re-deriving thresholds; absent token lists never negate a reported anchoring concern.
- The HITL dashboard may project up to eight persisted tool results for the current plan step, paired with their corresponding attempts and omitting request arguments. It may show a prior adverse Judge assessment only for an exact operation-payload digest match, with evidence-selection comparability stated separately. Actor-reported blocking and a continuation policy directive remain distinct displayed signals; the dashboard does not infer a new runtime decision from either.
- `configs/opa/layer2_governance/data.json` is the sole source for static organizational constraints and bounded external-action revision limits consumed by OPA
- `runtime_warnings.py` centralizes warning suppression needed before importing selected LangGraph/LangChain dependencies
- `graph.py` contains the current LangGraph wiring for the planner/actor/governance/tool/replanner/human-review/external-execution loop:
  `START -> bootstrap_from_intake -> planner -> actor -> governance`
  `governance -> { actor | tool_gateway | replanner | human_review | external_execution | finalize_bootstrap }`
  `human_review -> { planner | actor | tool_gateway | replanner | governance | external_execution | finalize_bootstrap }`
  `external_execution -> { external_execution | human_review | finalize_bootstrap }`
  `tool_gateway -> observe_tool_result -> { actor | governance | replanner }`
  `replanner -> actor`
  `finalize_bootstrap -> END`

Reference implementation note:

- `docs/guidelines/plan-and-execute.ipynb` is the main local reference for the internal planner/executor split
- when tools are introduced, prefer the LangGraph/LangChain pattern of binding real tools to the executor instead of describing the available tools only in natural language inside the prompt
- the executor prompt may still be richer and more architecture-specific than the minimal prompt shown in the tutorial


### `configs`

Contains configuration inputs that influence runtime behavior.

Current relevant file:

- `configs/layer1_execution_policies.json`
- `configs/layer2_tool_gateway.json`
- `configs/opa/layer2_governance/data.json`

Current relevant files:

- `tests/test_layer1_intake.py`
- `tests/layer2_runtime_fixtures.py`
- `tests/test_layer2_core_runtime.py`
- `tests/test_layer2_planning_runtime.py`
- `tests/test_layer2_actor_runtime.py`
- `tests/test_layer2_tool_runtime.py`
- `tests/test_layer2_governance_runtime.py`
- `tests/test_layer2_hitl_runtime.py`
- `tests/test_layer2_architecture.py`
- `tests/test_layer1_layer2_integration.py`
- `tests/test_layer2_hitl_dashboard.py`
- `tests/test_hitl_control_plane.py`
- `tests/test_main_layer2_dashboard.py`
- `tests/test_review_decision_inbox.py`
- `tests/test_layer2_oversight.py`
- `tests/test_layer2_trace.py`
- `tests/test_layer5_evaluation.py`
- `tests/test_tool_gateway_mcp.py`
