# AGENTS.md

## Purpose

This repository contains a Master's thesis proof of concept whose central
architectural contribution is an **auditable Human Oversight and Human-in-the-Loop
control engine for autonomous AI agents**.

The autonomous planner/actor/tool runtime is essential: it is the real system being
supervised and the experimental substrate that makes oversight meaningful. The thesis
contribution is not the agent alone, but the control architecture that observes its
execution, evaluates bounded signals, applies policy, escalates at justified
boundaries, binds human decisions to exact candidates, and preserves replayable
accountability.

The project is not a generic chatbot. Its priorities, in contribution order, are:

- auditable oversight and policy enforcement;
- reason-scoped, typed Human-in-the-Loop intervention;
- uncertainty-aware escalation and bounded external-action governance;
- evidence, trajectory, semantic assessment, traceability, and replay;
- evaluation through human-intervention, feedback, and uncertainty metrics;
- an autonomous plan-and-execute agent runtime as the supervised substrate;
- controlled task intake and external-capability execution.

The current design references are:

- `docs/architecture/architecturev3.md`
- `training_plan.md`


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
- `execution/providers/bedrock.py`
- `replanning.py` (compatibility facade)
- `actor_contract.py`
- `src/layer3_tool_bus_external_capabilities/tool_contract.py`
- `src/layer3_tool_bus_external_capabilities/tools/gateway.py`
- `governance.py`
- `src/layer4_state_evidence_observability/behavioral/`
- `judge/`
- `src/layer4_state_evidence_observability/oversight/`
- `execution/providers/bedrock_runtime.py`
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
- `src/layer4_state_evidence_observability/infrastructure/` owns checkpoint composition and persistence ports
- `composition.py` is the explicit dependency-injection entry point for runtime construction

Current design:

- `domain/` owns the canonical bootstrap state, runtime flags, plan models, actor execution records, review contracts, `TaskState`, and serialization recovery; `state.py` is an import-compatible facade
- `domain/plan.py` carries only semantic `PlanStep` fields: identifier, objective, rationale, expected outcome, and runtime-owned status
- `runtime/` owns lifecycle, planner, actor, governance, tool observation, replanning services, and thin LangGraph node adapters; the retired `nodes.py` facade must not be restored
- `planning/` defines the planner port, prompt builders, the default OpenAI-backed planner, and replanning adapters
- `execution/factory.py` builds the configured actor; concrete Bedrock/OpenAI adapters, prompts, action schemas, response parsing, and result construction live under `execution/`
- `actor_contract.py` passes observed concrete evidence and the semantic current step; it never passes a universal evidence rank or claim ceiling
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
- `execution/providers/bedrock.py` defines the Amazon Bedrock actor adapter, while `execution/providers/bedrock_runtime.py` owns the Converse client and settings for the approved Nova 2 Lite EU inference profile
- `runtime/governance_service.py` applies the OPA-first governance result and persists static, dynamic, behavioral, and judge records before routing
- `src/layer4_state_evidence_observability/infrastructure/runtime_artifacts.py` configures the local official LangGraph SQLite saver plus the per-thread runtime artifact store; `src/layer4_state_evidence_observability/infrastructure/checkpointing.py` can select the official PostgreSQL saver for deployed environments
- `src/layer4_state_evidence_observability/observability/` defines the typed, JSON-exportable Layer 4 trace projections for Layer 2 runs, builders, and compact views used for debugging, thesis evidence, HITL payloads, and future evaluation; `trace_summary.py` produces a human-readable Markdown summary written alongside the full JSON trace as `trace_summary.md`; `governance_narrative.py` produces `governance_narrative.md`, a turn-by-turn account of what the actor and governance said to each other, built from the exported trace (so it can also be rendered for already-archived runs) rather than from the live log stream - it collapses routine passes into one line, quotes the actor's own recorded rationale, names the exact flagged values and the operator's decision, and never replaces `governance_exchange.md`, which stays the complete authoritative audit stream; the retired `trace.py` facade must not be restored
- `schemas/layer2_trace.schema.json` is the closed Draft 2020-12 contract for runtime-produced `layer2_trace.v6`; the current Layer 5 importer accepts this version only and does not silently migrate legacy traces or exchange-only runs. Retrieval-kind, evidence-burden, error-code, and reason-code additions are explicit v6 contract fields; a future incompatible trace change must bump the version.
- Fields added to `v6` after it shipped (`toolObservation.retrieval_kind`, `evidenceBurden.insufficiency_declaration_permitted`) are declared but **not** `required`. Absence means the producing runtime predates the field, never that the value is false or unknown-and-substitutable. Keeping them optional is what makes the addition genuinely backward compatible: artifacts already exported as `layer2_trace.v6` continue to validate, so one version label never covers two mutually invalid shapes. Making such a field `required` is an incompatible change and must bump the trace version together with the Layer 5 importer. `tests/test_layer2_trace.py` pins this rule.
- `hitl/dashboard.py` defines the dashboard-facing snapshot model derived from `TaskState`, governance, oversight, and HITL review payloads, including bounded Evidence/Trajectory diagnostics, judge findings, and selected persisted evidence references for reviews that have no external proposal
- `main_layer2_dashboard.py` acts as a standalone multi-thread HITL control plane backend that serves the React dashboard build and exposes review inbox, detail, and typed decision-submission APIs; it validates and durably enqueues decisions but never executes or resumes the LangGraph runtime
- `src/layer4_state_evidence_observability/infrastructure/review_decision_inbox.py` is the durable control-plane transport between the dashboard and the runtime owner; it provides idempotent request-bound submissions, atomic claiming, delivery status, and a single-owner lease, but it is not runtime state and cannot reconstruct a graph
- the original `main_layer2.py` process remains the execution owner while a thread is interrupted, consumes the queued decision, and applies `Command(resume=...)` through the official checkpointer while streaming continuation progress in the same terminal
- the dashboard removes a submitted request from active approvals immediately, exposes it as a non-interactive completed-review item for 2 minutes, and then removes that transient acknowledgement; canonical run completion remains distinct from review completion
- `frontend/dashboard` contains the standalone React + TypeScript + Tailwind + daisyUI dashboard frontend that is built into static assets and served by `main_layer2_dashboard.py`
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


## Coding Standards

### General

- Prefer clear, explicit code over clever code.
- Keep architecture-aligned naming.
- Avoid hidden behavior and implicit coupling.
- Preserve separation of concerns between:
  - state models
  - runtime logic
  - graph wiring
  - infrastructure concerns

### Maintainability and Modularity

- Organize the implementation by bounded layer: Layer 2 owns domain/runtime/planning/execution/governance/HITL orchestration; Layer 3 owns Tool Bus and external capabilities; Layer 4 owns state, evidence, persistence, and observability.
- Keep one composition root responsible for selecting concrete adapters and wiring dependencies. Domain and application modules must not construct infrastructure clients directly.
- Dependencies must point inward: domain models know no frameworks; application services depend on domain contracts and ports; adapters depend on ports; LangGraph wiring composes them.
- Keep LangGraph nodes thin. A node may adapt graph state, call one application service, and record the result, but business rules belong in domain or application modules.
- Define ports beside the capability that consumes them and implement adapters in an `adapters` or `infrastructure` package.
- Prefer feature-local modules over broad utility modules. Do not create generic `utils.py`, `helpers.py`, or `common.py` dumping grounds.
- Split files when they combine more than one independently testable responsibility, require unrelated dependencies, or force readers to cross provider, parsing, policy, and domain concerns in one module.
- Treat roughly 400-500 lines as a review signal, not an automatic failure. A larger file needs one cohesive responsibility and an explicit reason to remain whole.
- Keep package depth shallow. Use at most two meaningful levels inside a capability unless an adapter family genuinely requires another level.
- Public package APIs must be explicit through small `__init__.py` files. Never import the entire runtime graph merely to expose a domain type.
- Preserve backward-compatible facades during incremental refactors, mark them as compatibility surfaces, and remove them only after callers and tests migrate.
- Avoid circular imports through contract placement and dependency direction, not through scattered runtime imports or `Any` casts.
- Configuration loading, environment access, network I/O, subprocesses, and database access are infrastructure concerns and must stay outside pure domain modules.
- External adapters must expose bounded timeouts, structured failures, and deterministic normalization into typed domain results.
- Shared mutable singletons are forbidden. Runtime services must be safe to construct per worker and explicit about stateful dependencies.
- Every new capability package must include a short README or package-level documentation covering its responsibility, public entry point, dependencies, and extension procedure.
- Mirror production package boundaries in tests so each capability can be tested without booting the full LangGraph runtime.
- Add architecture tests for important forbidden dependencies. Directory structure alone is not considered an enforced boundary.
- Refactor by vertical slice: add characterization tests, introduce the new module, keep a compatibility facade, migrate callers, then remove dead code.
- Do not extract a network microservice solely to reduce file size. Require an independent lifecycle, scaling profile, security boundary, ownership boundary, or failure policy.

### Canonical Ownership and Reuse

- Search the repository before adding a model, parser, validator, normalizer, serializer, factory, configuration loader, prompt projection, or test fixture. Extend the canonical implementation when the responsibility already exists.
- Every invariant must have one authoritative owner. Other modules call or import that owner; they must not reproduce the same rule with locally renamed helpers.
- Repeated validation, normalization, serialization, dependency construction, environment parsing, response-envelope construction, or state-transition logic is actionable duplication and must be consolidated in the feature that owns the invariant.
- Keep distinct code only when the semantics, trust boundary, or failure policy is genuinely different. For example, strict live-request validation and permissive checkpoint recovery may remain separate, but the reason must be documented and covered by tests.
- Do not create generic abstractions merely because two short blocks look similar. Extract around a stable domain concept, shared invariant, or repeated change axis; incidental syntactic similarity is not sufficient.
- Prefer a small typed function or service with explicit inputs over inheritance, global registries, implicit hooks, or copy-and-edit variants.
- Configuration must have one canonical source of truth. Variants should be bounded overlays or generated artifacts, not complete copied registries that can drift independently.
- Production definitions must not be duplicated in Python defaults and configuration files. Fail clearly when the canonical configuration is missing or invalid.
- Test modules must reuse shared builders, stubs, fixtures, and factories from feature-aligned test support modules. Do not copy a helper into a second test file.
- Frontend presentation mappings, labels, state classes, and icons must be centralized when more than one component renders the same domain state.

### Dead Code and API Lifecycle

- Do not add speculative code for possible future use. Every new symbol, field, branch, configuration key, export, and dependency must have a current caller and a test or a documented external contract.
- Remove superseded implementations in the same vertical slice after all callers migrate. Do not leave old private helpers, fallback branches, aliases, configuration fields, or commented-out code behind.
- A public export is not evidence that code is alive. Exported symbols must be referenced by the repository or explicitly documented and tested as compatibility surfaces for external consumers.
- Compatibility facades must be narrow, clearly identified as compatibility surfaces, and protected by a test that explains why the legacy import path is still required. Remove the facade once the documented migration condition is satisfied.
- When removing a capability, update its code, tests, exports, configuration, examples, READMEs, architecture documentation, and debug scripts together so documentation cannot keep dead concepts alive.
- Required protocol and framework callback parameters that are intentionally unused should use underscore-prefixed names when the signature permits it. If an exact keyword name is required, use a narrow `# noqa` and make the protocol reason evident from the surrounding contract.
- Do not silence dead-code or lint findings broadly. Ignore markers must be local to the intentional compatibility or protocol seam and must never hide an entire file or category.
- Delete unused dependencies and imports immediately. A dependency is justified only by an active production path, development gate, or test.

### Mandatory Change Workflow

- Before editing, locate the canonical owner and all callers with repository search. Check production code, tests, configuration, frontend code, documentation, and package exports.
- Add or identify characterization tests before consolidating behavior whose semantics are not already explicit.
- Implement the new canonical surface, migrate every caller, then remove the displaced implementation and stale exports in the same change.
- Re-run repository search for the removed symbol, copied block, retired path, and old configuration key. Zero stale references are required unless a documented compatibility test intentionally retains one.
- Review the final diff for new near-duplicates, pass-through wrappers, unused fields, unreachable branches, copied test setup, and documentation drift. A green test suite alone is not sufficient evidence of maintainability.
- Keep changes cohesive. If cleanup reveals a separate architectural problem, report or isolate it instead of mixing unrelated redesign into the same refactor.
- Preserve intentional compatibility, checkpoint recovery, and adapter protocol seams, but identify them explicitly in the handoff so they are not mistaken for accidental duplication or dead code later.

### Required Quality Gates

- Every code-changing task must finish with `ruff check src tests`.
- Every code-changing task must run `vulture src tests --min-confidence 80`; investigate every result and remove it or document the narrow protocol/compatibility reason for retaining it.
- Run `pytest -q tests` for repository-wide refactors. For a genuinely isolated change, focused tests are acceptable during iteration, but the final handoff must state exactly what was and was not run.
- When `frontend/dashboard` changes, run both `npm test -- --run` and `npm run build` from that directory.
- Run `git diff --check` and resolve whitespace errors. Platform line-ending advisories alone are not failures.
- Run architecture tests whenever dependency direction, composition, public facades, package ownership, or layer boundaries change.
- Do not claim that cleanup is complete while a high-confidence dead-code finding, exact duplicate, stale reference, or unexplained compatibility shim remains.
- The final handoff must list the canonical surfaces introduced, dead surfaces removed, intentional repetitions retained, and the exact validation results.

### Comments

- Prefer short `#` comments over docstrings for internal implementation explanation.
- Keep comments focused on why the code exists or what contract it enforces.
- Avoid low-value comments that restate the code mechanically.
- Prefer one clear explanatory comment over duplicated comment-plus-docstring blocks when the code is internal.
- Every new code addition must include short `#` comments above each new `def` and on non-obvious lines or blocks where readability would otherwise suffer.

### Typing

- Prefer strong typed models over anonymous dictionaries where contracts matter.
- Use enums instead of free-form strings for stable runtime markers and decision types.
- Use `dict[str, Any]` only as an extension field, not as the primary domain contract.

### Dataclasses and Models

- Layer 2 runtime state uses dataclasses.
- Layer 1 canonical payloads use Pydantic models.
- Preserve this split unless there is a strong architectural reason to change it.


## Layer-Specific Rules

### Layer 1 Rules

- `Layer 1` must output a canonical `IntakeResult`.
- `ExecutionPolicy` is system-controlled, not inferred freely from the user task.
- `confidence_threshold` and read-only mode are configuration-driven.
- Layer 1 execution policy currently carries confidence threshold and read-only mode only; tool allowlists/denylists are deferred and should not be exposed to the actor contract.
- `context` may be empty without forcing `partial`.
- `success_criteria` and missing constraints for sensitive tasks are more important than missing context.

### Layer 2 Rules

- `PlanStep` declares its semantic objective, rationale, expected outcome, runtime-owned `status`, factual claim scope (`produces_factual_claims`, `evidence_burden`), and the explicit boolean `produces_governed_external_effect_proposal`. An `execute_after_governance` trajectory must contain exactly one marked step and it must be terminal; a `proposal_only` trajectory contains none. Planner and replanner receive one bounded correction attempt, while runtime preflight revalidates custom adapters before actor execution. The evidence burden remains mandatory for factual steps; an explicit TaskSpec evidence floor raises a weaker declared burden and is preserved across replanning. A governed external-effect proposal may have no declared burden, but it still activates the Evidence structural families.
- Tool observations record a stable identifier, concrete observed content, an optional locator, and the adapter-owned `retrieval_kind`. Search snippets never count as retrieved document content or independent source origins; runtime orchestration validates and records the adapter classification without reclassifying content by prefix.
- Actor completion selects explicit `evidence_refs` and exposes `actor_self_confidence`. It does not declare evidence sufficiency or route itself; OPA interprets declared burden satisfaction and contextual uncertainty.
- Required OPA pillars are event-sensitive: Trajectory is always active across all events (in exploratory turns observing liveness only; at semantic/operational commitment adding convergence rules); Evidence is active on `STEP_COMPLETED`, on `PROPOSE_ACTION` when the action carries factual content (communication body or database statement), and on governed external candidates; external candidates additionally require the semantic judge. Factual completion and governed external-effect proposals both activate Evidence structural families 2–5. Behavioral and risk remain deferred shadow observations; Evidence is active when required and is not shadow-only.
- `TaskSpec` carries only goal, context, constraints, sensitivity, success criteria, and execution intent. It contains no static task taxonomy or duplicate operation manifest.

- `TaskState` is the central runtime contract.
- New runtime data should be modeled explicitly before being spread through nodes.
- `graph.py` should not contain business logic beyond routing and LangGraph integration.
- `runtime/node_adapters.py` must stay thin and must not become a dumping ground for business logic.
- the current implementation scope covers the current planner/actor/tool/replanner/governance slice
- the active baseline includes `bootstrap_from_intake -> planner -> actor -> governance`, with governed read-only acquisition continuing through `tool_gateway -> observe_tool_result -> { actor | governance | replanner }`
- the active baseline also includes the real `replanner` branch triggered by `NEEDS_REPLAN`
- the active baseline also includes a typed governance branch after `actor` that can continue, replan, escalate, or abort
- governance sends deterministic static facts, active evidence/trajectory facts, shadow risk/behavioral observations, OPA-requested semantic judge output, and runtime recovery facts; OPA returns the only final directive
- the OPA decision preserves the static read-only envelope as an explicit trace signal; Python validates and applies the returned directive without running a second final-decision algorithm
- externality or write compatibility flags alone must never force deny or HITL; static hard deny is deterministic, judge is mandatory for governed-external execution candidates, and risk/behavioral cannot influence the directive
- the static-policy read-only database profile allowlist is configuration (`data.policy_config.static_policy.read_only_profile_ids` in `configs/opa/layer2_governance/data.json`), never a literal profile id in Rego, so a new read-only profile has to be declared to be recognized rather than silently bypassing the gate; the legacy `write_blocked`/`hitl_mandatory`/`deny_reason` static fields (redundant with `hard_deny` and `summary`) were removed from the pillar result and from `decision_parser.py`, which now derives directive/summary from `hard_deny` and `summary` alone
- governance evaluates semantic and operational boundaries, not thought boundaries: `INTERMEDIATE_OUTPUT` returns directly to the actor and neither evidence nor trajectory may classify narration length as degradation
- external communication drafts are explicit runtime state; partial draft assembly does not invoke the judge, while each review-ready execution candidate is projected into a digest-bound `ExternalOperationProposal`
- communication proposal bodies are canonical and complete: normalization may standardize line endings but must never silently truncate semantic content. Only `payload_preview` is bounded; the capability schema rejects bodies above its explicit limit before connector invocation.
- `CommunicationOperationPayload.body_format` (`plain` | `markdown`) is author-declared, part of the payload and therefore of the digest, so the judge assesses it and the operator reviews it. An unknown declaration degrades to `plain` rather than being trusted. The connector renders accordingly: the `text/plain` part stays byte-identical to the approved canonical body and any HTML alternative is a deterministic presentation-only rendering of that same content, so what governance approved and what the recipient receives can never diverge in substance. The renderer escapes all embedded markup and links only `http(s)` targets.
- `execute_after_governance` is a terminal runtime invariant: a plain local
  `STEP_COMPLETED` cannot satisfy the task, and `COMPLETED` requires a typed
  external-operation receipt with status `executed`, or an `indeterminate`
  receipt explicitly reconciled by a digest-bound human decision
- normal read-only evidence acquisition is never `action_churn` and never an
  `external_candidate`; churn is computed only across `GOVERNED_EXTERNAL`
  execution candidates for the same logical operation, and completed human review
  starts a new replanning/churn episode without erasing whole-run counters
- audit-safe intermediate outputs may span multiple actor turns, but an exact replay
  on unchanged state is treated as a recoverable stuck actor protocol rather than progress
- the planner/executor split should remain aligned with `docs/guidelines/plan-and-execute.ipynb` unless there is a clear thesis-driven reason to diverge
- planner output must remain semantic and typed
- planner and replanner output only semantic steps and never introduce compliance, policy-simulation, or human-review meta-steps
- planner output must not contain concrete tool calls, executable payloads, or framework-specific actions
- runtime planning services must preserve the planner-owned semantic steps without compaction, compression, insertion, or lexical rewriting
- actor execution receives the full semantic plan as context but may commit completion only for the current plan step
- actor output must remain typed and audit-safe even when backed by an LLM; intermediate output may summarize meaningful working progress without being forced into premature completion
- the current Layer 2 actor backend defaults to Amazon Bedrock Nova 2 Lite in `eu-central-1`; deployments may override the Bedrock model or inference profile and region through configuration
- planner and replanner remain OpenAI-backed for now
- actor provider selection is controlled by `LAYER2_ACTOR_PROVIDER`, with supported values `bedrock` and `openai`; Bedrock is the default
- malformed provider JSON that omits the runtime action envelope should be recovered only when it is clearly completion-shaped; otherwise it should stop conservatively instead of being treated as normal progress
- provider failures that return no usable runtime action and no actor output are runtime recovery events; they should route to replanning/retry rather than opening a human-review gate with an empty draft
- actor execution may produce local outputs, request later capability use, ask clarification, or signal replan
- actor execution should record audit-safe scratchpad entries rather than hidden chain-of-thought
- externally verifiable claims must remain no stronger than the concrete observed content that supports them; task-specific terminology must not be introduced by generic runtime prompts
- plan steps complete only through the unambiguous `STEP_COMPLETED` or final completion outcome and the resulting OPA directive
- tool observations carry `observation_id`, concrete `content`, an optional `locator`, and typed `retrieval_kind`; Layer 4 persists the same discriminator in snapshots and `layer2_trace.v6`
- intake fields remain task instructions and are never materialized as tool observations
- the adapter that performed an action creates its observations; runtime orchestration validates and records them without semantic reclassification
- OPA may request more evidence from concrete completion facts and declared/TaskSpec-derived burden satisfaction; Python exports raw verifiable facts and consumes OPA's validated evidence-request ceiling result, while Rego owns thresholds and the final directive. The evidence-gap decision carries every active deficit code from `evidence.result.reason_codes` alongside the generic gate code, and remediation is built from those decision-level codes, never from the pillar's raw `deficit_codes` — a code OPA excluded (e.g. via a disabled structural family) must never reach the actor as if it still gated the decision. The TaskSpec floor is an intake-contract safeguard, not a read of OPA configuration.
- provider protocol errors (unknown evidence IDs, malformed action JSON, missing required fields) trigger a bounded local retry (max 1 retry attempt on the same step and state, without bumping `progress_epoch` or invoking replanner); upon retry exhaustion, `provider_protocol_failure` persists and OPA evaluates the state directly without Python forced replanning or forced HITL
- `GovernanceActionFeedback` carries `judge_summary`, `judge_rationale`, and structured `findings`; `prompt_projection.py` projects full judge feedback and `previous_rejected_draft` into the `ActorTask` prompt payload during revision turns; the actor system prompt instructs to revise the existing draft rather than rewriting blindly, to address every finding explicitly, and to never resubmit an unchanged payload or re-assert compliance without substantive correction
- a candidate payload plus Evidence-selection binding matching previously rejected feedback sets `repeated_rejected_payload_digest=true` in Trajectory; unchanged resubmission likewise requires the same payload and selection digest seen by prior governance. OPA refuses only truly redundant Judge admission and maps rejected or unchanged resubmissions to distinct non-authorizing review reasons, never to `replan_request_stalled`
- a repeated judge `needs_revision` without new observations, tool calls, or completed steps is a known non-deterministic boundary and is surfaced as `unresolvable_revision` for HITL; it never authorizes external execution
- every new `StepCompletionCandidate` persists the Evidence snapshot and selection digests; Evidence recomputes and verifies candidate, operation, complete observation manifest, and actor-selected references before Judge admission
- when governance first materializes a review-ready communication proposal after actor completion, it atomically seals that operation id/digest and the recomputed Evidence selection digest into the still-unbound completion candidate before Evidence and Judge run; an already-bound candidate is immutable and drift fails closed
- human approval may authorize a new `execute_after_governance` request only for the exact digest of a `capability_approval_required` gate; Judge, trajectory, recovery, unavailable-pillar, unknown, and legacy external pauses are non-authorizing
- external execution revalidates the completion candidate, operation digest, Evidence snapshot/selection and terminal plan owner before connector invocation; a mismatched/untyped connector receipt or connector exception after invocation becomes `indeterminate` reconciliation state instead of raising after a possible external effect
- Evidence evaluates 5 structural families: referential integrity & digest binding, declared/TaskSpec-floor burden coverage measured on the actor-selected references (independent sources, retrieved content), with `retrieved_independent_sources_count` exported as a non-deciding universe diagnostic that separates "never retrieved" from "retrieved but not cited"; the pre-turn actor completion gate can only observe the universe and must stay strictly weaker than this coverage check, never opening a completion Evidence would reject, observation structural quality (title-only fragility vs full content), the declared non-circularity field, and literal anchoring of extractable tokens (distinguishing task-anchored literals from truly unanchored literals and selection from universe). Literal anchoring splits by token kind into two OPA-facing classes: an unanchored locator (a cited URL never retrieved) is an `evidence_violation`, resolvable by the actor fetching or dropping it, and stays in the bounded `request_more_evidence` loop; an unanchored non-locator value (a number, date, or currency amount) is an `evidence_concern`, because no further retrieval turns a computed value into a quoted one and the actor's only move would be deleting a claim that may be correct, so it routes directly to `wait_human_review` (`evidence_anchoring_requires_review`) without spending a remediation round. `evidence_gap` remains the historical union of both classes for judge-admission and completion gating. A human approval on an anchoring review is remembered by binding `adjudicated_evidence_anchoring_digest` to the approved evidence-selection digest, so the same concern does not re-open on the same selection; a replan clears it, since a replacement trajectory produces different claims. SQL statement text is excluded from the anchoring target: a database statement is a request, not a claim, and its literals are selection criteria the actor authored, not values a source must support. It never performs semantic claim entailment or selects a directive. Search discovery is typed separately from retrieved documents. The freshness structural family (source-timestamp windows, staleness, future-dated sources) was retired: it activated only on an explicit "last N days" task constraint, which no evaluation scenario declares, and its `freshness_enforcement_enabled=false` config still fed deficits to the actor that OPA had already excluded from the decision — Python overruling the engine. The three `freshness_*` `GovernanceReasonCode` values and the schema's `freshness_window_days` field stay declared, unused, and optional so archived pre-retirement traces keep validating against the closed `layer2_trace.v6` schema; nothing in the current runtime emits them. Literal anchoring includes exact URL-token identity, task contract text, the selected locator, and attributed source timestamp as provenance (still carried as observation context, not as a freshness input); structural section numbers are not factual claims. A numeric literal is anchored by quantity, not surface form: grouping and decimal separators are resolved by locale convention (a lone separator before exactly three digits admits both readings) so "141.006", "141,006" and "141006" match, trailing zeros are insignificant so "97%" matches "97.0%", and a share written in prose anchors its symbol form so a source saying "28 percent" grounds an agent writing "28%". Because only the concern class reaches a person, each flagged value is exported with the sentence it was written in plus a deterministic lookup of the same quantity in the actor-selected sources (`unanchored_literal_excerpts`): a hit is reported as a passage to check with its locator, never as "the source said X", and an explicit absence is itself the decisive signal. A bare token is not adjudicable, and a nearest-match guess would have a reviewer approve against a comparison the runtime invented.
- JudgeContext carries the full semantic plan, completed-step summaries, and every canonical ToolObservation available at the boundary through a deterministic, digest-bound `judge_evidence_bundle.v1` that is embedded in the request and persisted in run artifacts. The model never searches the run and can cite only bundle-listed `evidence_id` values; content digests remain runtime-only metadata. Selected refs mark actor-declared reliance only; process narration, tool arguments, counters, retry/replan history and shadow assessments are excluded. Evidence-base omissions/truncation are explicit and lead to `uncertain`, not silently reduced coverage.
- `INTERMEDIATE_OUTPUT` is an audit-safe working update that returns directly to the actor and never completes a plan step; `STEP_COMPLETED` is the explicit local completion type
- trace IDs must remain immutable across runtime resumes and retries; `trace_id` represents the semantic run boundary and is generated by `bootstrap_from_intake`
- trace models must explicitly link `observation_id`, `step_id`, and `evidence_ref` in the canonical JSON structure to ensure reconstruction and observability
- executed step history and current plan step cursor must remain explicit in `TaskState`
- when tools are added, do not rely on a prompt-only tool inventory as the primary mechanism; prefer structured tool binding in the LangGraph/LangChain style
- prompt text may contain policy, behavioral, and architecture constraints, but should not replace the runtime-level tool contract
- the current tool surface is read-only and externally conservative
- read-only capability use is currently explicit and typed through `ToolRequest`, `ToolExecutionResult`, and `ToolObservation`
- tool availability is governed by the Tool Gateway registry and governance read-only/write policy; Layer 1 no longer passes actor-facing allowed/disallowed tool lists
- tool observation should preserve source-level provenance without cloning one aggregate tool transcript across multiple returned sources
- the Tool Gateway supports a configured MCP local test server with read-only tools:
  `mcp.local_test.echo`, `mcp.local_test.read_fixture`, `mcp.local_test.text_stats`, and `mcp.local_test.calculate_expression`
- MCP tool calls must be server-configured, tool-allowlisted, read-only, and normalized back into `ToolExecutionResult`
- MCP authentication and per-principal authorization are not implemented yet; credentials and runtime identity must be added above the backend adapter later, not inside actor prompts or `ToolRequest`
- `replanner` may replace only the current and pending semantic trajectory; completed steps remain immutable history
- the actor may request `NEEDS_REPLAN` with a non-empty blocking reason; OPA admits the first request or a later request with a new observable execution basis, while repeated churn on an unchanged trajectory routes to HITL
- a new observable execution basis means a backend-reaching tool invocation, a new observation, or a completed step since the last replan checkpoint (human reviews do not count as a new factual basis on the world); neither the replanner's own `progress_epoch` bump nor its plan-signature change self-admit a subsequent replan request. The invocation count, not the length of the raw tool-result history, is what must advance: a locally settled outcome (a cache hit, a rejected duplicate, an unanchored-target rejection) still appends to that history without reaching a backend, so counting its length let a settled outcome satisfy this guard and admit the very replan it exists to refuse.
- trajectory convergence limits enforce bounded progress: `replan_ceiling_reached` counts every replaced trajectory in the active reviewed episode, regardless of who raised it — it must not require a coincident actor `NEEDS_REPLAN` request, because the tool gateway's own protocol replan (§ below) never presents as one by the time governance next runs, and gating the ceiling on that signal let a run replace its trajectory without bound; `action_churn` is scoped to the current external operation; `consecutive_tool_failures` counts executed backend failures only, remains observational below its higher HITL threshold, and never forces an automatic replan
- read-only exploration is bounded: `ToolDisposition.reached_backend` is the canonical split between a real capability call and every locally settled outcome (cache hit, duplicate, circuit-open, unanchored-target rejection), and both the per-request anti-churn guard and Trajectory's `failed_tool_call_count`/`synthetic_tool_result_count` reuse it so a new local rejection kind can never fall outside every convergence rule by construction; the anti-churn guard counts repeats within the current plan step, not the current `progress_epoch`, because the epoch advances on every new observation and an actor interleaving one productive call resets an epoch-scoped counter before it can ever reach its ceiling; `current_step_tool_attempt_count` (all attempts, including locally rejected ones, on the current step) trips `step_exploration_budget_exhausted` above `max_tool_attempts_per_step_before_review` unless the turn is itself a completion, since Evidence is the substantive check on that boundary; the tool gateway additionally caps settled (non-backend-reaching) attempts on one target at run scope, independent of the plan step — once a target is exhausted, further identical requests receive an explicitly terminal refusal and the current step is deliberately kept open (never replanned again for that target) so its exploration budget keeps climbing toward the ceiling above. This is enforcement of whether one execution is admitted, not a directive threshold: how many replaced trajectories a run may accumulate remains OPA's `replan_ceiling_reached` alone, and the gateway concedes at most one protocol replan per target before that ceiling is the only remaining terminating condition
- `WAITING_HUMAN_REVIEW` is now the typed pause state that feeds the LangGraph `interrupt / resume` HITL path
- governance is active as an explicit runtime node and uses OPA over HTTP as the deterministic static policy engine target
- Python fact normalization must never reinterpret or relax the OPA decision
- the default LangGraph checkpointer is the official durable local SQLite saver rather than a process-local in-memory saver; production deployments may select the official PostgreSQL saver
- the default checkpoint file is `artifacts/runtime/checkpoints/layer2_checkpoints.sqlite3`; LangGraph owns its checkpoint schema and serialization, and pending interrupts must never be reconstructed from artifact JSON
- the runtime persists per-thread artifacts under `artifacts/runtime/<thread_id>/`, including the canonical trace, a human-readable `trace_summary.md`, the operator-facing `governance_narrative.md`, dashboard snapshot, runtime summary, and pending HITL payload when present
- the behavioural SQLite store lives under `artifacts/runtime/behavioral/behavioral.sqlite3` by default and must remain a read model rather than the primary runtime source of truth
- active evidence/trajectory interpretation must remain deterministic and explainable; Python exports concrete facts, Rego owns their directive mapping, and shadow pillars (risk and behavioral) may export diagnostic labels and facts but never directives
- Trajectory Health uses only whole-run observable execution dynamics at semantic boundaries (liveness on exploratory turns, convergence on commitment points) and never treats narrative turns, elapsed thinking, or point confidence alone as degradation.
- The semantic judge is invoked only after OPA admits a complete governed-external candidate. It receives authoritative task data, full semantic plan, completed-step summaries, all canonical observations and selected references, same-operation revision findings, and the exact digest-bound payload; it never receives scratchpad, raw tool history/arguments, runtime events, counters, retry/replan history, or shadow assessments.
- runtime trace export must be read-only over `TaskState`; it must not mutate runtime state or become a parallel source of truth
- `main_layer2.py` may print trace views, but reusable trace construction belongs in `src/layer4_state_evidence_observability/observability/`
- `resume_layer2_flow` should prefer the direct LangGraph `invoke(Command(resume=...))` result when it contains the canonical `task_state`, because interrupted checkpoints may briefly expose only a partial snapshot wrapper during resume; the CLI-owned HITL path may stream the same command to keep node progress visible in the original terminal
- `ABORTED` is the hard-denial runtime state for governance decisions that must not be conflated with review
- HITL is wired as `interrupt / resume`; governance now pauses in typed states and resumes from typed human decisions
- the local Layer 2 CLI no longer auto-launches the HITL control plane; while a thread is in `WAITING_HUMAN_REVIEW`, the original CLI remains alive as its leased runtime owner and polls the durable decision inbox, while the standalone dashboard discovers pending reviews from persisted runtime artifacts and only submits decisions
- owner leases must be heartbeated while a resumed provider/tool call is in flight; after owner loss, `--attach-thread <thread_id>` may recover only a checkpoint that is still waiting for the exact request, and the inbox may reclaim that same request-bound command from `claimed` or `failed` delivery state
- the current proof-of-concept inbox adapter is local SQLite; any multi-host deployment must bind `ReviewDecisionInboxPort` to shared transactional storage rather than combining PostgreSQL checkpoints with host-local inbox files
- `runtime_summary.json` is now inbox-oriented and includes `task_goal`, `review_summary`, `pending_request_id`, `allowed_decisions`, `external_communication_phase`, `has_pending_human_review`, `termination_reason`, and `updated_at`
- the dashboard snapshot is the sole artifact DTO for pending-review data; the standalone dashboard may read legacy `pending_human_review.json` files but never writes new ones. Every pending HITL review renders a generic operator context with task, current step, policy reason, resume state, allowed decisions, compact Evidence/Trajectory facts, judge findings, and bounded selected evidence references; external-operation panels remain optional specialized detail

### HITL Rules

- HITL decisions are reason-scoped: unsafe/uncertain semantic review exposes modify/replan/abort; capability approval exposes approve/modify/abort; clarification and indeterminate-receipt reconciliation expose only their applicable typed actions.
- An indeterminate-receipt reconciliation review carries its own `external_execution_reconciliation_required` reason and its own summary built from the persisted receipt (connector reference, error code); it must never inherit the preceding governance reason codes, because the approval gate asks whether the effect may happen while reconciliation asks whether it already did. The reason is listed among the codes that cannot authorize external execution: reconciliation settles an outcome and never re-authorizes the connector.
- HITL payloads must remain readable, compact, and include exact operation/digest/findings when an external proposal exists.
- Human decisions should remain canonical and typed.
- `modify_and_continue` is available for every modifiable external proposal, creates a new digest, invalidates prior approval/judge state, and returns to governance.
- `approve_continue_read_only` must reject an unchanged mutating proposal.


## Debugging Guidance

Debug the runtime from:

- `src/main_layer2.py`

Do not debug compatibility facades such as `state.py` as standalone entrypoints.

Useful breakpoint locations:

- `src/main_layer2.py`
- `src/layer2_runtime_governance/runtime/node_adapters.py`
- `src/layer2_runtime_governance/runtime/actor_service.py`
- `src/layer2_runtime_governance/runtime/governance_service.py`
- `src/layer2_runtime_governance/domain/task_state.py`
- `src/layer2_runtime_governance/planning/__init__.py`
- `src/layer2_runtime_governance/execution/factory.py`
- `src/layer2_runtime_governance/planning/replanning.py`
- `src/layer3_tool_bus_external_capabilities/tools/gateway.py`
- `src/layer4_state_evidence_observability/oversight/__init__.py`
- `src/layer4_state_evidence_observability/observability/builder.py`
- `src/layer4_state_evidence_observability/observability/trace_summary.py`

In particular:

- inspect the output of Layer 1 before graph invocation
- inspect `bootstrap_from_intake`
- inspect `planner`
- inspect `actor`
- inspect `replanner` when `NEEDS_REPLAN` is emitted
- inspect `observe_tool_result` when evidence is unexpectedly weak or missing
- inspect `TaskState.from_intake`

When debugging in VS Code:

- place breakpoints on executable lines inside function bodies
- do not place the main breakpoint on the `def` line if you want local variables immediately available


## Running the Project

### Layer 1

Run the Layer 1 example:

```powershell
.\.venv\Scripts\python.exe src\main_layer1.py
```

### Layer 2

Run Layer 2 bootstrap, planner, actor, tool loop, and replanner with real Layer 1 parsing:

```powershell
.\.venv\Scripts\python.exe src\main_layer2.py --task "Find official sources about the Digital Markets Act and produce a short summary." --context "Prefer official EU sources."
```

Run Layer 2 bootstrap, planner, actor, tool loop, and replanner with deterministic demo intake:

```powershell
.\.venv\Scripts\python.exe src\main_layer2.py --demo-intake-profile research --task "Find official sources about the Digital Markets Act and produce a short summary."
```

Install the standalone HITL dashboard frontend dependencies:

```powershell
npm install --prefix frontend/dashboard
```

Build the standalone HITL dashboard frontend:

```powershell
npm run build --prefix frontend/dashboard
```

Run the standalone local HITL control plane dashboard:

```powershell
.\.venv\Scripts\python.exe src\main_layer2_dashboard.py --open-browser
```

Recover an interrupted thread whose original runtime owner is no longer alive:

```powershell
.\.venv\Scripts\python.exe src\main_layer2.py --attach-thread <thread_id>
```


## Testing

Run Layer 1 tests:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_layer1_intake -v
```

Run Layer 2 tests:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_layer2_runtime_governance -v
```

Run the Layer 1 -> Layer 2 integration tests:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_layer1_layer2_integration -v
```

Run all current tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```


## Environment Notes

The project currently uses:

- Python `>=3.12,<3.14` in local virtual environment `.venv`; Python 3.13 is recommended and declared in `.python-version`
- `requirements-production.txt` for runtime dependencies and `requirements-dev.txt` for test/development-only dependencies such as `jsonschema`
- OpenAI API for the real Layer 1 parser
- OpenAI API for the default Layer 2 planner
- Amazon Bedrock for the current Layer 2 actor backend through `eu.amazon.nova-2-lite-v1:0` in `eu-central-1`
- OpenAI API for the default Layer 2 replanner
- OpenAI API for the `web_search` path in the default Layer 2 tool gateway
- Python standard library HTTP/HTML extraction for the `fetch_url_text` path in the default Layer 2 tool gateway
- LangGraph for Layer 2 runtime orchestration
- durable local SQLite LangGraph checkpoint persistence for the current local runtime graph

Environment variables may be loaded from:

- `.env`
- `src/.env`

Expected secret:

- `OPENAI_API_KEY`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

Expected Bedrock actor runtime:

- optional overrides:
  - `LAYER2_ACTOR_PROVIDER=bedrock`
  - `LAYER2_ACTOR_BEDROCK_MODEL_ID=eu.amazon.nova-2-lite-v1:0` (or another Bedrock Converse-compatible model/inference profile available in `AWS_REGION`)
  - `AWS_REGION=eu-central-1`
  - `AWS_SESSION_TOKEN=` when temporary AWS credentials are used


## Change Policy

When making changes:

1. preserve alignment with `docs/architecture/architecturev3.md`
2. preserve alignment with `training_plan.md`
3. prefer evolving existing contracts over adding parallel ad-hoc ones
4. keep runtime behavior explainable
5. update tests when contracts change
6. keep the implementation compact, but not ambiguous
7. update `AGENTS.md` whenever the repository behavior, architecture, contracts, commands, workflows, or conventions materially change

When a change introduces an important architectural step, debugging aid, workflow clarification, or visual explanation that could be useful later in the thesis, it is acceptable and encouraged to produce supporting documentation material inside `docs/`.

This can include:

- markdown notes
- diagrams
- HTML visualizations
- HTML slide decks
- interactive HTML documents or prototypes
- screenshots or other visual references
- other thesis-useful documentation artifacts

This does **not** mean producing documentation for every minor change. The threshold should be pragmatic:

- create documentation when it materially improves explainability, traceability, or thesis reuse
- do not create documentation for routine edits that add little long-term value

Avoid:

- introducing broad untyped dictionaries where stable models already exist
- mixing graph wiring and business logic
- silently changing runtime contracts without updating tests

`AGENTS.md` is not optional documentation. It must be treated as a living operational contract for the repository. If a change makes any section inaccurate, that file must be updated in the same change set.


## Recommended Next Steps

The current most relevant near-term evolution path is:

1. keep Layer 1 stable and explicit
2. keep the Layer 1 -> Layer 2 bootstrap-planner-actor-tool-replanner contract stable
3. harden the governance contract and policy bundle boundaries before adding more policy dimensions
4. calibrate active evidence/trajectory and shadow operational consequence facts against exported traces
5. harden the standalone HITL dashboard and richer operator workflows on top of the existing `interrupt / resume` path
6. calibrate governed-external judge findings and revision-loop thresholds against real traces before widening the tool surface
7. strengthen observability and Evidence Integrity & Provenance before widening the tool surface
