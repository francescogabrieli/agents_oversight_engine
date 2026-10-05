# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Master's thesis PoC: an auditable **Human Oversight / Human-in-the-Loop control engine for
autonomous AI agents**. The autonomous LangGraph planner/actor/tool runtime is the supervised
substrate; the thesis contribution is the governance layer (OPA/Rego-owned decisions), typed
HITL intervention, evidence/trajectory assessment, and replayable trace observability around it.

**`AGENTS.md` at the repo root is the authoritative, actively maintained architecture and
conventions document — read it before making non-trivial changes.** It is long and detailed
(layer ownership, coding standards, canonical-owner rules, quality gates, layer-specific
invariants). This file only summarizes what's needed to get oriented and run things; when this
file and `AGENTS.md` disagree, `AGENTS.md` wins and should be treated as out of date here rather
than vice versa. `AGENTS.md` itself must be updated in the same change set whenever architecture,
contracts, commands, or conventions materially change — do this when you make such a change.

Other references: `docs/architecture/architecturev3.md` (design), `training_plan.md`
(objectives/priorities), `docs/guidelines/plan-and-execute.ipynb` (planner/executor pattern
reference), `evaluation_task_pool.md` (canonical evaluation task pool/protocol).

## Environment Setup

Python `>=3.12,<3.14`, 3.13 recommended (`.python-version`). Virtualenv at `.venv`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-production.txt   # runtime deps
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt          # + tests/schema validation (jsonschema, ruff, vulture)
```

Secrets/config load from `.env` or `src/.env`: `OPENAI_API_KEY` is used by the
default Layer 2 actor (`LAYER2_ACTOR_PROVIDER=openai`, model
`LAYER2_ACTOR_OPENAI_MODEL=gpt-6-luna`). AWS credentials and
`LAYER2_ACTOR_BEDROCK_*` settings apply only to explicitly selected historical
Bedrock comparison runs.

## Common Commands

Run Layer 1 example:

```powershell
.\.venv\Scripts\python.exe src\main_layer1.py
```

Run Layer 2 (bootstrap → planner → actor → tool loop → replanner → governance):

```powershell
.\.venv\Scripts\python.exe src\main_layer2.py --task "Find official sources about the Digital Markets Act and produce a short summary." --context "Prefer official EU sources."
.\.venv\Scripts\python.exe src\main_layer2.py --demo-intake-profile research --task "..."   # deterministic demo intake
.\.venv\Scripts\python.exe src\main_layer2.py --attach-thread <thread_id>                    # recover interrupted thread after owner loss
```

Standalone HITL dashboard (React frontend served by the Python backend):

```powershell
npm install --prefix frontend/dashboard
npm run build --prefix frontend/dashboard
.\.venv\Scripts\python.exe src\main_layer2_dashboard.py --open-browser
```

Tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v         # all tests
.\.venv\Scripts\python.exe -m unittest tests.test_layer1_intake -v  # single module
.\.venv\Scripts\python.exe -m pytest -q tests                       # pytest form used by quality gates
.\.venv\Scripts\python.exe -m pytest -q tests/test_layer2_governance_runtime.py -k some_test  # single test
```

Lint / dead-code / frontend gates (run at the end of every code-changing task, per `AGENTS.md`):

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m vulture src tests --min-confidence 80
git diff --check
# if frontend/dashboard changed:
npm test -- --run --prefix frontend/dashboard
npm run build --prefix frontend/dashboard
```

## Architecture (high level)

Five numbered layers, each with a distinct owner; Layer 2 is orchestration/governance, not "the
whole app" — it consumes typed services from Layers 3 and 4 rather than owning capability
execution or persistence itself.

- **Layer 1 — Task Intake** (`src/layer1_intake`): parses raw task input (OpenAI-backed) into a
  canonical `IntakeResult`/`TaskSpec`/`ExecutionPolicy`, external to the LangGraph runtime.
- **Layer 2 — Runtime & Governance** (`src/layer2_runtime_governance`): the live LangGraph graph
  (`graph.py`) wiring `bootstrap_from_intake → planner → actor → governance`, with branches to
  `tool_gateway → observe_tool_result`, `replanner` (on `NEEDS_REPLAN`), `human_review`
  (`interrupt`/`resume`), and `external_execution`. Governance sends typed static/evidence/
  trajectory/judge facts to an external **OPA/Rego** policy engine, which is the sole final
  directive authority — Python never recreates a competing decision combiner. Subpackages:
  `domain/` (framework-independent contracts), `runtime/` (graph node adapters, thin), `planning/`
  (planner/replanner, OpenAI-backed), `execution/` (actor factory + Bedrock/OpenAI adapters),
  `governance_components/` (OPA input/adapter/orchestration), `composition.py` (DI root).
- **Layer 3 — Tool Bus / External Capabilities** (`src/layer3_tool_bus_external_capabilities`):
  governed capability execution. Actors/governance never call connectors or MCP servers directly
  — everything goes through the Tool Gateway registry (`tools/gateway.py`,
  `configs/layer2_tool_gateway.json`). `external_operations/` owns typed proposals, SQL/comms
  normalizers, idempotency records; `external_capability_mcp/` owns SMTP email + DB-statement MCP
  tools (disabled by default).
- **Layer 4 — State, Evidence & Observability** (`src/layer4_state_evidence_observability`):
  oversight/evidence contracts, behavioral (shadow-only SQLite) read model, `observability/`
  (typed trace export, `trace_summary.py`), `infrastructure/` (LangGraph checkpoint persistence,
  runtime artifacts under `artifacts/runtime/<thread_id>/`, the review decision inbox).
- **Layer 5 — Evaluation** (`src/layer5_evaluation_continuous_improvement`): offline-only;
  ingests exported `layer2_trace.v6` artifacts into a dedicated SQLite projection and produces
  metrics/annotations/reports. Never enters the live runtime loop; Continuous Improvement is
  deferred/unimplemented.
- **`services/policy_decision_point`**: the deployable stateless OPA service. LangGraph is the
  Policy Enforcement Point and applies only validated typed OPA directives.
- **`src/governance_policy_service`**: runtime-independent HTTP transport contract to OPA; must
  never import Layer 2 runtime state.
- **`frontend/dashboard`**: standalone React + TypeScript + Tailwind + daisyUI HITL dashboard,
  built to static assets and served by `main_layer2_dashboard.py`. The dashboard only reads
  persisted runtime artifacts and enqueues typed decisions into the durable review inbox — it
  never executes or resumes the LangGraph runtime itself; `main_layer2.py`'s original process
  remains the execution owner for an interrupted thread.

Config sources of truth: `configs/layer1_execution_policies.json`,
`configs/layer2_tool_gateway.json`, `configs/opa/layer2_governance/data.json` (static OPA
constraints). Trace contract: `schemas/layer2_trace.schema.json` (closed schema, `v6` only, no
silent migration of legacy traces).

## Key Conventions (see `AGENTS.md` for the full, authoritative list)

- Dependencies point inward: domain → application/ports → adapters; LangGraph nodes stay thin
  (adapt state, call one service, record result — no business logic in `graph.py` or
  `node_adapters.py`).
- No generic `utils.py`/`helpers.py` dumping grounds; split files by cohesive responsibility
  (~400-500 lines is a review signal, not a hard limit).
- Search for and extend the canonical owner of a model/parser/validator/normalizer before adding
  a new one — repeated validation/normalization/serialization logic is duplication, not
  independent code.
- No speculative code: every new symbol/field/branch/config key needs a current caller and a
  test. Remove superseded implementations and stale exports in the same change; compatibility
  facades must be narrow and test-protected.
- Layer 2 runtime state uses dataclasses; Layer 1 canonical payloads use Pydantic models.
- OPA/Rego is the sole final governance directive authority; Python normalizes/validates but
  must never relax or recompute the decision.
- Prefer short `#` comments over docstrings; comment the *why*, not the *what*.
