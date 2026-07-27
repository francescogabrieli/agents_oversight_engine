# AGENTS.md

## Purpose

This repository contains a thesis-oriented proof of concept for a governed AI agent architecture with strong human oversight.

The project is not a generic chatbot. It is an architectural prototype focused on:

- controlled task intake
- runtime orchestration
- governance and policy enforcement
- human-in-the-loop review
- evidence tracking and observability
- future evaluation and continuous improvement
- autonomous plan execution with audit-safe self-monitoring

The current design references are:

- `architecture/architecturev3.md`
- `training_plan.md`


## Current Scope

At the moment, the repository mainly covers:

- `Layer 1 - Task Intake`
- `Layer 2 - Bootstrap + Planner + Actor/Tool Loop + Replanner + Governance`
- offline evaluation over exported Layer 2 traces

The currently implemented Layer 2 slice is not just a single planner/executor pass.
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
- external communication plan semantics remain planner-owned; runtime compaction must not introduce facts or rewrite the requested communication
- typed runtime trace export through `observability/`, surfaced by `src/main_layer2.py`
- offline trace evaluation and reporting through `evaluation/`, surfaced by `src/main_layer2_evaluation.py`

These are the folders to treat as the active implementation baseline:

- `src/layer1_intake`
- `src/layer2_runtime_governance`
- `src/main_layer1.py`
- `src/main_layer2.py`
- `src/main_layer2_dashboard.py`
- `src/main_layer2_evaluation.py`
- `src/governance_policy_service`
- `services/policy_decision_point`

Supporting material lives in:

- `architecture/`
- `configs/`
- `docs/`
- `frontend/dashboard`
- `tests/`


## Architectural Intent

The repository follows the architecture described in `architecture/architecturev3.md`.
It should also remain aligned with the objectives and design priorities described in `training_plan.md`, especially around:

- autonomous agent behavior with strong human oversight
- uncertainty management and escalation logic
- traceability, transparency, and organizational accountability
- observability and evaluation of behavior under uncertainty

High-level macro-blocks:

1. `Task Intake`
2. `Runtime & Governance`
3. `Tool Bus / External Capabilities`
4. `State, Evidence & Observability`
5. `Evaluation & Continuous Improvement`

Important implementation principles:

- `Layer 1` is external to the LangGraph runtime.
- `Layer 2` is the live orchestration layer and is implemented with LangGraph.
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
- `acting.py` (compatibility facade)
- `replanning.py` (compatibility facade)
- `actor_contract.py`
- `tool_contract.py`
- `tool_gateway.py`
- `governance.py`
- `behavioral/`
- `judge/`
- `oversight/`
- `evaluation/`
- `execution/providers/bedrock_runtime.py` (`bedrock_client.py` is a compatibility facade)
- `infrastructure/runtime_artifacts.py` (`persistence.py` is a compatibility facade)
- `infrastructure/runtime_warnings.py`

Modular capability packages introduced for the active Layer 2 baseline:

- `domain/` exposes framework-independent views of canonical planning, execution, evidence, review, and task-state contracts
- `runtime/` owns graph contracts, routing, node adapters, and thread context
- `tools/` owns the Tool Gateway registry, validation, backend adapters, and content extractors; `tool_gateway.py` is a compatibility facade
- `execution/` owns actor provider configuration, prompts, action schemas, response parsing, result construction, and provider adapters; `acting.py` is a compatibility facade and actor factory
- `governance_components/` owns policy contracts, inputs, adapters, OPA-first evaluation, response parsing, and fail-closed handling
- `observability/` owns immutable trace models, read-only builders, compact views, and JSON serialization
- `src/evaluation_service/` owns runtime-independent trace discovery, loading, legacy normalization, per-run evaluation, metric aggregation, and report serialization; `aggregate.py` is a thin compatibility facade
- `infrastructure/` owns checkpoint composition and persistence ports
- `composition.py` is the explicit dependency-injection entry point for runtime construction

Current design:

- `domain/` owns the canonical bootstrap state, runtime flags, plan models, actor execution records, review contracts, `TaskState`, and serialization recovery; `state.py` is an import-compatible facade
- `domain/plan.py` carries only semantic `PlanStep` fields: identifier, objective, rationale, expected outcome, and runtime-owned status
- `runtime/` owns lifecycle, planner, actor, governance, tool observation, replanning services, and thin LangGraph node adapters; the retired `nodes.py` facade must not be restored
- `planning/` defines the planner port, prompt builders, the default OpenAI-backed planner, and replanning adapters
- `acting.py` preserves the public actor imports and builds the configured actor; concrete Bedrock/OpenAI adapters, prompts, action schemas, response parsing, result construction, and evidence guards live under `execution/`
- `actor_contract.py` passes observed concrete evidence and the semantic current step; it never passes a universal evidence rank or claim ceiling
- `planning/replanning.py` defines the replanner port, prompt builders, the default OpenAI-backed replanner, and a local conservative fallback replanner for tests/offline use; `replanning.py` preserves the legacy import path
- `actor_contract.py` defines the typed handoff from planner state into actor-facing execution context for the current plan step, including observed evidence, scratchpad entries, and prior step validations
- `tool_contract.py` defines typed tool requests, tool results, and observed evidence contracts
- `tool_gateway.py` contains the read-only operational tool surface currently available to the runtime, including `web_search`, `fetch_url_text`, and configured read-only MCP test tools behind the gateway registry
- `configs/layer2_tool_gateway.json` is the active Tool Gateway registry for configured native, HTTP, OpenAI, and MCP-backed tools
- MCP is usable as a governed backend behind the Tool Gateway; the actor must never call MCP servers directly
- `governance_components/` defines typed policy contracts, OPA inputs and adapter, OPA-first orchestration, decision parsing, fail-closed handling, and final governance traces; `governance.py` retains checkpoint aliases only
- `governance_policy_service` owns the runtime-independent HTTP transport contract for the external OPA Policy Decision Point; it must not import Layer 2 runtime state
- `services/policy_decision_point` is the deployable, stateless OPA service boundary; LangGraph remains the Policy Enforcement Point and applies only validated typed directives
- OPA/Rego is the sole final directive authority; Python may normalize and validate transport data but must not recreate a competing final decision combiner
- `behavioral/` defines the shadow-only SQLite historical read model, similarity/deviation/novelty scoring, persistence, and counterfactual recommendations with no directive influence
- `judge/` defines the generic governed-external semantic assessor; OPA owns invocation and directive mapping
- `oversight/contracts.py` retains typed assessment/checkpoint contracts; `signal_services/` produces raw facts and OPA owns scoring, thresholds, judge requirements, and directives
- `oversight/` preserves compatibility contracts; evidence and trajectory are active, while `operational_risk` exports shadow consequence facts and is not applicable outside governed external operations
- `external_operations/` owns immutable typed proposals, execution intent projection, SQL AST and communication/generic normalizers, a dedicated capability registry, durable idempotency records, the external executor port, and the capability-bound MCP adapter
- `src/external_capability_mcp/` owns the productive typed MCP tools for SMTP email and profile-bound database statements; both are disabled by default and credentials remain infrastructure configuration
- `execution/providers/bedrock_runtime.py` defines the Amazon Bedrock Converse adapter used by the Layer 2 actor with the approved Nova 2 Lite EU inference profile; `bedrock_client.py` is import-compatible
- `runtime/governance_service.py` applies the OPA-first governance result and persists static, dynamic, behavioral, and judge records before routing
- `infrastructure/runtime_artifacts.py` configures the local official LangGraph SQLite saver plus the per-thread runtime artifact store; `persistence.py` is import-compatible and `infrastructure/checkpointing.py` can select the official PostgreSQL saver for deployed environments
- `observability/` defines the typed, JSON-exportable Layer 2 trace models, builders, and compact views used for debugging, thesis evidence, HITL payloads, and evaluation; the retired `trace.py` facade must not be restored
- `schemas/layer2_trace.schema.json` is the closed Draft 2020-12 contract for runtime-produced `layer2_trace.v3`; only `evaluation_service` may project legacy v1/v2 traces into that v3 read model
- `evaluation/` defines the offline trace evaluation pipeline that reads exported trace JSON and produces JSON and Markdown reports, including typed-vs-heuristic evidence mismatch accounting
- `hitl_dashboard.py` defines the dashboard-facing snapshot model derived from `TaskState`, governance, oversight, and HITL review payloads
- `main_layer2_dashboard.py` now acts as a standalone multi-thread HITL control plane backend that serves the React dashboard build, exposes the review inbox/detail/resume API, and resolves resume actions from persisted checkpoints on demand
- `main_layer2_dashboard.py` now also persists post-resume artifacts through the same runtime artifact store used by the dashboard inbox/detail API, so HITL decisions immediately refresh the observed thread state after agent continuation
- `main_layer2_dashboard.py` rebuilds a persisted thread directly from the configured official LangGraph checkpointer, so the standalone dashboard can resume runs created by a separate `main_layer2.py` process without relying on stale in-memory state or trace artifacts
- the dashboard review inbox is intentionally action-oriented and now hides pending-review threads whose persisted `updated_at` is older than 2 minutes; old artifacts remain on disk and detail views can still be resolved directly by thread id
- `frontend/dashboard` contains the standalone React + TypeScript + Tailwind + daisyUI dashboard frontend that is built into static assets and served by `main_layer2_dashboard.py`
- `configs/opa/layer2_governance/data.json` is the sole source for active evidence/trajectory thresholds, static organizational constraints, and revision/provider-loop limits consumed by OPA
- `infrastructure/runtime_warnings.py` centralizes warning suppression needed before importing selected LangGraph/LangChain dependencies
- `graph.py` contains the current LangGraph wiring for the planner/actor/governance/tool/replanner/human-review/external-execution loop:
  `START -> bootstrap_from_intake -> planner -> actor -> governance -> { tool_gateway | replanner | human_review | external_execution | finalize_bootstrap }`
  `human_review -> { planner | actor | tool_gateway | replanner | governance | external_execution | finalize_bootstrap }`
  `external_execution -> { external_execution | human_review | finalize_bootstrap }`
  `tool_gateway -> observe_tool_result -> actor`
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


### `docs`

Contains diagrams and visual aids.

Current relevant files:

- `docs/guidelines/plan-and-execute.ipynb`
- `docs/guidelines/plan-and-execute.jpg`
- `docs/deck/planner_use_cases.html`
- `docs/deck/confidence_risk_trajectory_deck.html`
- `docs/governance_hitl_contract.md`
- `docs/tool_gateway_strategy.md`
- `docs/flow_details.md`
- `architecture/e2e_real_task.md`


### `tests`

Contains unit and integration-style tests for the implemented layers.

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
- `tests/test_layer2_evaluation.py`
- `tests/test_layer2_hitl_dashboard.py`
- `tests/test_hitl_control_plane.py`
- `tests/test_main_layer2_dashboard.py`
- `tests/test_layer2_oversight.py`
- `tests/test_layer2_trace.py`
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

- Organize Layer 2 by bounded capability: domain state, planning, execution, tools, governance, HITL, runtime orchestration, observability, and infrastructure.
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
- `confidence_threshold` and budget are configuration-driven.
- Layer 1 execution policy currently carries budget, confidence threshold, and read-only mode only; tool allowlists/denylists are deferred and should not be exposed to the actor contract.
- `context` may be empty without forcing `partial`.
- `success_criteria` and missing constraints for sensitive tasks are more important than missing context.

### Layer 2 Rules

- `PlanStep` is now semantic-only: `step_id`, `objective`, `rationale`, `expected_outcome`, and runtime-owned `status`. Evidence levels, precision ceilings, and compliance step kinds are not planner contracts.
- Observed evidence records orthogonal facts (`content_state`, `source_authority`, `document_type`, `extractability`, and `citation_alignment`); no universal evidence ranking is active in the runtime or OPA.
- Actor completion selects explicit `evidence_refs` and exposes `actor_self_confidence`. It does not declare evidence sufficiency or route itself; OPA interprets the contextual uncertainty signal.
- Required OPA pillars are event-sensitive: read-only tool acquisition uses static plus minimal trajectory, progress uses trajectory, completion uses evidence plus trajectory, and governed external candidates require judge. Behavioral and risk remain deferred shadow observations.
- `TaskSpec.requested_operations` describes requested effects independently of `domain_type`; an email proposal requires an explicit requested external-communication operation and still passes judge, digest binding, HITL, and idempotent execution.

- `TaskState` is the central runtime contract.
- New runtime data should be modeled explicitly before being spread through nodes.
- `graph.py` should not contain business logic beyond routing and LangGraph integration.
- `runtime/node_adapters.py` must stay thin and must not become a dumping ground for business logic.
- the current implementation scope covers the current planner/actor/tool/replanner/governance slice
- the active baseline includes `bootstrap_from_intake -> planner -> actor -> governance -> tool_gateway -> observe_tool_result -> actor`
- the active baseline also includes the real `replanner` branch triggered by `NEEDS_REPLAN`
- the active baseline also includes a typed governance branch after `actor` that can continue, replan, escalate, or abort
- governance sends deterministic static facts, active evidence/trajectory facts, shadow risk/behavioral observations, OPA-requested semantic judge output, and runtime recovery facts; OPA returns the only final directive
- the OPA decision preserves the static read-only envelope as an explicit trace signal; Python validates and applies the returned directive without running a second final-decision algorithm
- externality or write compatibility flags alone must never force deny or HITL; static hard deny is deterministic, judge is mandatory for governed-external execution candidates, and risk/behavioral cannot influence the directive
- governance still evaluates every actor turn, but intermediate local outputs that explicitly remain in recovery (`continue_acting` / `request_more_evidence`) should prefer local continuation over premature human-review escalation unless a hard policy or explicit blocking condition requires review
- external communication drafts are explicit runtime state; partial draft assembly does not invoke the judge, while each review-ready execution candidate is projected into a digest-bound `ExternalOperationProposal`
- the planner/executor split should remain aligned with `docs/guidelines/plan-and-execute.ipynb` unless there is a clear thesis-driven reason to diverge
- planner output must remain semantic and typed
- planner and replanner output only semantic steps and never introduce compliance, policy-simulation, or human-review meta-steps
- planner output must not contain concrete tool calls, executable payloads, or framework-specific actions
- small source-plus-short-summary tasks may be deterministically compacted from an over-decomposed source/extraction/synthesis pipeline into a proportional two-step trajectory
- actor execution must operate on the current plan step, not on the full task as a second planner
- actor output must remain typed and compact even when backed by an LLM
- actor prompts receive concrete observed evidence attributes and explicit evidence identifiers; OPA alone interprets their sufficiency for a completion
- the current Layer 2 actor backend is Amazon Bedrock with Nova 2 Lite through the fixed `eu.amazon.nova-2-lite-v1:0` inference profile in `eu-central-1`
- planner and replanner remain OpenAI-backed for now
- actor provider selection is controlled by `LAYER2_ACTOR_PROVIDER`, with supported values `bedrock` and `openai`; Bedrock is the default
- malformed provider JSON that omits the runtime action envelope should be recovered only when it is clearly completion-shaped; otherwise it should stop conservatively instead of being treated as normal progress
- provider failures that return no usable runtime action and no actor output are runtime recovery events; they should route to replanning/retry rather than opening a human-review gate with an empty draft
- actor execution may produce local outputs, request later capability use, ask clarification, or signal replan
- actor execution should record audit-safe scratchpad entries rather than hidden chain-of-thought
- official implementation pages and procedural regulations should be described as procedural sources, not as direct enforcers, unless the source explicitly says so
- when the actor sees an implementation-oriented DMA source, it should phrase the enforcement description as procedural or procedural-enforcement context rather than direct enforcement unless the source text explicitly supports the stronger claim
- plan steps complete only through the unambiguous `STEP_COMPLETED` or final completion outcome and the resulting OPA directive
- observed evidence carries orthogonal concrete attributes: content state, source authority, document type, extractability, citation alignment, locator, excerpt, tool, and provenance
- observed evidence now also carries an explicit provenance kind: `intake_provided` for authoritative Layer 1 task inputs and `tool_observation` for tool-derived runtime evidence; `source_tool` is therefore optional and only set for tool-observed evidence
- bootstrap materializes authoritative task-provided facts into initial observed evidence without assigning a universal support level
- when a step is explicitly short, brief, concise, or high-level, the actor should reduce claim precision before requesting more evidence; named obligation examples such as self-preferencing, interoperability, data portability, ranking, non-discriminatory access, or transparent terms should appear only when directly present in observed evidence or specifically requested
- governance guardrails must remain proportional and must not create an evidence loop while the actor is asking for the read-only tool needed to acquire evidence
- source discovery alone is not factual support; simple URL discovery is weaker than extracted content and should not justify detailed factual completion by itself
- OPA may request more evidence only from concrete completion facts, such as invalid selected references or missing usable selected evidence; Python does not infer semantic precision requirements
- `INTERMEDIATE_OUTPUT` is progress only and never completes a plan step; `STEP_COMPLETED` is the explicit local completion type
- executed step history and current plan step cursor must remain explicit in `TaskState`
- when tools are added, do not rely on a prompt-only tool inventory as the primary mechanism; prefer structured tool binding in the LangGraph/LangChain style
- prompt text may contain policy, behavioral, and architecture constraints, but should not replace the runtime-level tool contract
- the current tool surface is read-only and externally conservative
- read-only capability use is currently explicit and typed through `ToolRequest`, `ToolExecutionResult`, and `ObservedEvidence`
- tool availability is governed by the Tool Gateway registry and governance read-only/write policy; Layer 1 no longer passes actor-facing allowed/disallowed tool lists
- tool observation should preserve source-level provenance without cloning one aggregate tool transcript across multiple returned sources
- the Tool Gateway supports a configured MCP local test server with read-only tools:
  `mcp.local_test.echo`, `mcp.local_test.read_fixture`, `mcp.local_test.text_stats`, and `mcp.local_test.calculate_expression`
- MCP tool calls must be server-configured, tool-allowlisted, read-only, and normalized back into `ToolExecutionResult`
- MCP authentication and per-principal authorization are not implemented yet; credentials and runtime identity must be added above the backend adapter later, not inside actor prompts or `ToolRequest`
- `replanner` may replace only the current and pending semantic trajectory; completed steps remain immutable history
- `max_steps = null` means the current runtime does not impose a hard semantic step ceiling
- `max_tool_calls = null` means the current runtime does not impose a hard tool-call ceiling
- `WAITING_HUMAN_REVIEW` is now the typed pause state that feeds the LangGraph `interrupt / resume` HITL path
- governance is active as an explicit runtime node and uses OPA over HTTP as the deterministic static policy engine target
- Python fact normalization must never reinterpret or relax the OPA decision
- the default LangGraph checkpointer is the official durable local SQLite saver rather than a process-local in-memory saver; production deployments may select the official PostgreSQL saver
- the default checkpoint file is `artifacts/runtime/checkpoints/layer2_checkpoints.sqlite3`; LangGraph owns its checkpoint schema and serialization, and pending interrupts must never be reconstructed from artifact JSON
- the runtime persists per-thread artifacts under `artifacts/runtime/<thread_id>/`, including the canonical trace, dashboard snapshot, runtime summary, and pending HITL payload when present
- the behavioural SQLite store lives under `artifacts/runtime/behavioral/behavioral.sqlite3` by default and must remain a read model rather than the primary runtime source of truth
- active evidence/trajectory interpretation must remain deterministic, explainable, and implemented only in Rego using `configs/opa/layer2_governance/data.json`; shadow pillars may export diagnostic labels but never directives
- trajectory-health turn pressure should stay forgiving enough that short source-plus-summary tasks can make a few recovery turns without being forced into replanning
- runtime trace export must be read-only over `TaskState`; it must not mutate runtime state or become a parallel source of truth
- `main_layer2.py` may print trace views, but reusable trace construction belongs in `observability/`
- `resume_layer2_flow` should prefer the direct LangGraph `invoke(Command(resume=...))` result when it contains the canonical `task_state`, because interrupted checkpoints may briefly expose only a partial snapshot wrapper during resume
- `main_layer2_evaluation.py` is the offline, trace-only evaluation entrypoint and must stay decoupled from the live runtime CLI
- `ABORTED` is the hard-denial runtime state for governance decisions that must not be conflated with review
- HITL is wired as `interrupt / resume`; governance now pauses in typed states and resumes from typed human decisions
- the local Layer 2 CLI no longer auto-launches the HITL control plane; the standalone dashboard process discovers new `WAITING_HUMAN_REVIEW` threads by polling persisted runtime artifacts
- `runtime_summary.json` is now inbox-oriented and includes `task_goal`, `review_summary`, `pending_request_id`, `allowed_decisions`, `external_communication_phase`, `has_pending_human_review`, and `updated_at`
- the dashboard snapshot is the sole artifact DTO for pending-review data; the standalone dashboard may read legacy `pending_human_review.json` files but never writes new ones

### HITL Rules

- Every HITL exposes the same six universally valid base decisions; review modes must not restrict them.
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
- `src/layer2_runtime_governance/acting.py`
- `src/layer2_runtime_governance/planning/replanning.py`
- `src/layer2_runtime_governance/tool_gateway.py`
- `src/layer2_runtime_governance/oversight/__init__.py`
- `src/layer2_runtime_governance/observability/builder.py`

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
  - `LAYER2_ACTOR_BEDROCK_MODEL_ID=eu.amazon.nova-2-lite-v1:0`
  - `AWS_REGION=eu-central-1`
  - `AWS_SESSION_TOKEN=` when temporary AWS credentials are used


## Change Policy

When making changes:

1. preserve alignment with `architecture/architecturev3.md`
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
7. strengthen observability and evidence quality assessment before widening the tool surface
