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

The currently implemented Layer 2 slice is not just a single planner/executor pass.
It already includes:

- typed planner output
- typed actor output
- runtime-bound read-only tool requests
- tool result observation into explicit evidence
- audit-safe scratchpad entries
- typed step self-validations
- an active `replanner` branch for `NEEDS_REPLAN`
- deterministic `confidence_risk_trajectory` oversight for evidence quality, operational risk, and trajectory health
- typed runtime trace export through `trace.py`, surfaced by `src/main_layer2.py`

These are the folders to treat as the active implementation baseline:

- `src/layer1_intake`
- `src/layer2_runtime_governance`
- `src/main_layer1.py`
- `src/main_layer2.py`

Supporting material lives in:

- `architecture/`
- `configs/`
- `docs/`
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
- for the `planner + actor` branch, follow `docs/plan-and-execute.ipynb` as the primary implementation pattern reference:
  - multi-step semantic planner
  - step-bounded executor
  - structured tool binding at the runtime level
- `HITL`, when activated later, should be implemented through `interrupt / resume`.
- `external_communication` is handled conservatively:
  - first evidence collection
  - then draft proposal
  - then human review before externally impactful execution
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


### `src/layer2_runtime_governance`

Purpose:

- initialize runtime state from Layer 1
- produce the first semantic planner snapshot
- execute the current semantic plan step through the actor
- execute read-only capability calls through `tool_gateway`
- observe tool results and feed them back into the actor loop
- replace the current and pending semantic trajectory through `replanner` when the actor signals `NEEDS_REPLAN`
- evaluate actor outcomes through a typed governance node backed by an OPA policy engine
- combine OPA hard policy decisions with deterministic adaptive oversight signals
- persist governance decisions in `TaskState` with an explicit `ABORTED` stop state for hard denials
- persist oversight assessments in `TaskState` for audit and thesis traceability
- maintain an audit-safe scratchpad and typed step self-validations during actor execution
- stop after the current planner/actor/tool slice reaches a stable stop point in canonical `TaskState`

Main files:

- `state.py`
- `nodes.py`
- `graph.py`
- `planning.py`
- `acting.py`
- `replanning.py`
- `actor_contract.py`
- `tool_contract.py`
- `tool_gateway.py`
- `governance.py`
- `oversight.py`
- `openrouter_client.py`
- `trace.py`
- `runtime_warnings.py`

Current design:

- `state.py` defines the bootstrap state contract, runtime flags, planner snapshot models, actor execution snapshot, tool loop state, observed evidence, audit-safe scratchpad entries, typed step validations, oversight history, and runtime step outcomes
- `nodes.py` contains `bootstrap_from_intake`, `planner`, `actor`, `governance`, `tool_gateway`, `observe_tool_result`, `replanner`, and bootstrap closure helpers
- `planning.py` defines the planner port, prompt builders, and the default OpenAI-backed planner
- `acting.py` defines the actor port, prompt builders, the temporary OpenRouter-backed actor, and the optional OpenAI-backed actor using runtime-bound tools instead of a prompt-only tool inventory
- `replanning.py` defines the replanner port, prompt builders, the default OpenAI-backed replanner, and a local conservative fallback replanner for tests/offline use
- `actor_contract.py` defines the typed handoff from planner state into actor-facing execution context for the current plan step, including observed evidence, scratchpad entries, and prior step validations
- `tool_contract.py` defines typed tool requests, tool results, and observed evidence contracts
- `tool_gateway.py` contains the read-only operational tool surface currently available to the runtime, including `web_search`, `fetch_url_text`, and configured read-only MCP test tools behind the gateway registry
- `configs/layer2_tool_gateway.json` is the active Tool Gateway registry for configured native, HTTP, OpenAI, and MCP-backed tools
- MCP is usable as a governed backend behind the Tool Gateway; the actor must never call MCP servers directly
- `governance.py` defines the typed policy input/output contracts, OPA HTTP policy engine, and governance decision mapping used after actor execution
- `oversight.py` defines deterministic adaptive oversight for `evidence_quality`, `operational_risk`, and `trajectory_health`, plus the combiner recommendation consumed by governance
- `openrouter_client.py` defines the OpenRouter HTTP adapter used by the temporary Layer 2 actor test backend
- `trace.py` defines the typed, JSON-exportable Layer 2 runtime trace contract used for debugging, thesis evidence, future HITL payloads, and evaluation
- `configs/layer2_oversight_policy.json` defines the explicit weights, thresholds, risk scores, sensitivity multipliers, and trajectory penalties used by the oversight layer
- `runtime_warnings.py` centralizes warning suppression needed before importing selected LangGraph/LangChain dependencies
- `graph.py` contains the current LangGraph wiring for the planner/actor/governance/tool/replanner loop:
  `START -> bootstrap_from_intake -> planner -> actor -> governance -> { tool_gateway | replanner | finalize_bootstrap }`
  `tool_gateway -> observe_tool_result -> actor`
  `replanner -> actor`
  `finalize_bootstrap -> END`

Reference implementation note:

- `docs/plan-and-execute.ipynb` is the main local reference for the internal planner/executor split
- when tools are introduced, prefer the LangGraph/LangChain pattern of binding real tools to the executor instead of describing the available tools only in natural language inside the prompt
- the executor prompt may still be richer and more architecture-specific than the minimal prompt shown in the tutorial


### `configs`

Contains configuration inputs that influence runtime behavior.

Current relevant file:

- `configs/layer1_execution_policies.json`
- `configs/layer2_tool_gateway.json`
- `configs/layer2_oversight_policy.json`


### `docs`

Contains diagrams and visual aids.

Current relevant files:

- `docs/plan-and-execute.ipynb`
- `docs/plan-and-execute.jpg`
- `docs/planner_use_cases.html`
- `docs/confidence_risk_trajectory_deck.html`
- `docs/tool_gateway_strategy.md`


### `tests`

Contains unit and integration-style tests for the implemented layers.

Current relevant files:

- `tests/test_layer1_intake.py`
- `tests/test_layer2_runtime_governance.py`
- `tests/test_layer1_layer2_integration.py`
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

- `TaskState` is the central runtime contract.
- New runtime data should be modeled explicitly before being spread through nodes.
- `graph.py` should not contain business logic beyond routing and LangGraph integration.
- `nodes.py` should not become a dumping ground for framework-specific plumbing.
- the current implementation scope covers the current planner/actor/tool/replanner/governance slice
- the active baseline includes `bootstrap_from_intake -> planner -> actor -> governance -> tool_gateway -> observe_tool_result -> actor`
- the active baseline also includes the real `replanner` branch triggered by `NEEDS_REPLAN`
- the active baseline also includes a typed governance branch after `actor` that can continue, replan, escalate, or abort
- governance now combines OPA hard policy with deterministic `confidence_risk_trajectory` oversight; OPA denials, required review, and clarification decisions remain non-negotiable
- the planner/executor split should remain aligned with `docs/plan-and-execute.ipynb` unless there is a clear thesis-driven reason to diverge
- planner output must remain semantic and typed
- planner output must not contain concrete tool calls, executable payloads, or framework-specific actions
- small source-plus-short-summary tasks may be deterministically compacted from an over-decomposed source/extraction/synthesis pipeline into a proportional two-step trajectory
- actor execution must operate on the current plan step, not on the full task as a second planner
- actor output must remain typed and compact even when backed by an LLM
- the current Layer 2 actor backend is OpenRouter with `openai/gpt-oss-120b:free` for first cloud-based tests; this remains a temporary test choice before moving to a stable large cloud model
- planner and replanner remain OpenAI-backed for now
- actor provider selection is controlled by `LAYER2_ACTOR_PROVIDER`, with supported values `openrouter` and `openai`
- malformed provider JSON that omits the runtime action envelope should be recovered only when it is clearly completion-shaped; otherwise it should stop conservatively instead of being treated as normal progress
- actor execution may produce local outputs, request later capability use, ask clarification, or signal replan
- actor execution should record audit-safe scratchpad entries rather than hidden chain-of-thought
- official implementation pages and procedural regulations should be described as procedural sources, not as direct enforcers, unless the source explicitly says so
- when the actor sees an implementation-oriented DMA source, it should phrase the enforcement description as procedural or procedural-enforcement context rather than direct enforcement unless the source text explicitly supports the stronger claim
- plan steps should be completed only after a typed `StepValidation` indicates that the objective is satisfied and evidence is sufficient
- observed evidence now carries an explicit support level (`local_only`, `source_discovery`, `high_level_summary`, `detailed_extracted`, `primary_legal`) so the runtime can reason about how much claim precision the evidence can safely support
- official timeline facts such as `entered into force`, `applies from`, `applies on`, and similar chronology statements are usually `detailed_extracted`; only threshold, article-level, or similarly strict legal claims should force `primary_legal`
- conservative high-level summaries can be supported by `high_level_summary` evidence even when they mention broad policy themes such as gatekeepers or obligation themes; only granular, specific, article-level, threshold, or clause-like claims should force `detailed_extracted` or `primary_legal`
- when a step is explicitly short, brief, concise, or high-level, the actor should reduce claim precision before requesting more evidence; named obligation examples such as self-preferencing, interoperability, data portability, ranking, non-discriminatory access, or transparent terms should appear only when directly present in observed evidence or specifically requested
- governance guardrails must remain proportional: multiple safe-by-design checks should not combine into an execution-degrading loop when the task can be completed by lowering precision to the available evidence level
- source discovery alone is not factual support; simple URL discovery is weaker than extracted content and should not justify detailed factual completion by itself
- actor completion outputs that are more specific than the available evidence support level should remain in-progress rather than complete the step, even when the LLM self-reports `objective_satisfied=true` and `evidence_sufficient=true`
- adaptive oversight may request more evidence when the evaluated step/output requires a stronger evidence support level than the observed evidence currently provides
- `INTERMEDIATE_OUTPUT` is a progress/local-output outcome and must not automatically complete the current plan step without positive validation
- when the last semantic step completes with positive validation, its output should be promoted into canonical `final_output_text` even if the actor result type is `INTERMEDIATE_OUTPUT`
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
- `max_steps` is enforced as a semantic plan-step ceiling in the current runtime
- `max_tool_calls = null` means the current runtime does not impose a hard tool-call ceiling
- `WAITING_HUMAN_REVIEW` currently acts as a typed stop/escalation state, not as a full `interrupt / resume` HITL implementation yet
- governance is active as an explicit runtime node and uses OPA over HTTP as the deterministic policy engine target
- adaptive oversight may only preserve or make the policy decision more conservative; it must never make an OPA decision more permissive
- oversight scoring must remain deterministic, explainable, and configured through `configs/layer2_oversight_policy.json`
- trajectory-health turn pressure should stay forgiving enough that short source-plus-summary tasks can make a few recovery turns without being forced into replanning
- runtime trace export must be read-only over `TaskState`; it must not mutate runtime state or become a parallel source of truth
- `main_layer2.py` may print trace views, but reusable trace construction belongs in `trace.py`
- `ABORTED` is the hard-denial runtime state for governance decisions that must not be conflated with review
- HITL is still not wired as `interrupt / resume`; governance currently stops in typed states and leaves human continuation for later

### HITL Rules

- Any externally impactful write-like action should be reviewable before execution.
- HITL payloads must remain readable and compact.
- Human decisions should remain canonical and typed.


## Debugging Guidance

Debug the runtime from:

- `src/main_layer2.py`

Do not debug `state.py` or `nodes.py` as standalone entrypoints.

Useful breakpoint locations:

- `src/main_layer2.py`
- `src/layer2_runtime_governance/nodes.py`
- `src/layer2_runtime_governance/state.py`
- `src/layer2_runtime_governance/planning.py`
- `src/layer2_runtime_governance/acting.py`
- `src/layer2_runtime_governance/replanning.py`
- `src/layer2_runtime_governance/tool_gateway.py`
- `src/layer2_runtime_governance/oversight.py`
- `src/layer2_runtime_governance/trace.py`

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

- Python in local virtual environment `.venv`
- OpenAI API for the real Layer 1 parser
- OpenAI API for the default Layer 2 planner
- OpenRouter API for the current Layer 2 actor test backend, currently `openai/gpt-oss-120b:free`
- OpenAI API for the default Layer 2 replanner
- OpenAI API for the `web_search` path in the default Layer 2 tool gateway
- Python standard library HTTP/HTML extraction for the `fetch_url_text` path in the default Layer 2 tool gateway
- LangGraph for Layer 2 runtime orchestration
- in-memory LangGraph checkpointing for the current local runtime graph

Environment variables may be loaded from:

- `.env`
- `src/.env`

Expected secret:

- `OPENAI_API_KEY`
- `OPENROUTER_API_KEY`

Expected temporary actor runtime for first tests:

- optional overrides:
  - `LAYER2_ACTOR_PROVIDER=openrouter`
  - `LAYER2_ACTOR_OPENROUTER_MODEL=openai/gpt-oss-120b:free`
  - `LAYER2_OPENROUTER_BASE_URL=https://openrouter.ai/api/v1`
  - `LAYER2_OPENROUTER_TIMEOUT_SECONDS=120`

Future actor runtime target:

- replace the temporary OpenRouter free actor backend with a stable large cloud model once the runtime contracts, governance, tool gateway, oversight, and trace surfaces are stable


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
4. evolve confidence, risk, and trajectory signals into explicit inputs for governance instead of ad hoc heuristics
5. separate `WAITING_HUMAN_REVIEW` into a future `interrupt / resume` path rather than only a terminal typed state
6. introduce LLM-as-a-judge only after the deterministic governance slice remains stable and testable
7. strengthen observability and evidence quality assessment before widening the tool surface
