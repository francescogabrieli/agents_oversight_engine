# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Context

This is a Master's Thesis proof of concept for an **auditable Human Oversight and
Human-in-the-Loop control engine for autonomous AI agents**. The autonomous
planner/actor/tool runtime is the necessary supervised system; the central thesis
contribution is the evidence-aware, trajectory-aware, policy-driven, and digest-bound
control architecture that governs its external effects and human escalation. The
repository demonstrates controlled intake, real plan execution, OPA-owned governance,
reason-scoped HITL, replayable traceability, and offline evaluation. The primary
reference is `AGENTS.md` - treat it as the authoritative operational contract.

## Commands

### Python (backend — use `.venv` on Windows)

```bash
# Run Layer 1 intake
.\.venv\Scripts\python.exe src\main_layer1.py

# Run Layer 2 full runtime
.\.venv\Scripts\python.exe src\main_layer2.py --task "..." --context "..."
# Or with deterministic demo intake profile (research | compliance | communication)
.\.venv\Scripts\python.exe src\main_layer2.py --demo-intake-profile research --task "..."

# Start HITL dashboard backend (serves React frontend + REST API)
.\.venv\Scripts\python.exe src\main_layer2_dashboard.py --open-browser

# Offline trace evaluation
.\.venv\Scripts\python.exe src\main_layer2_evaluation.py

# Run a single test module
.\.venv\Scripts\python.exe -m unittest tests.test_layer2_runtime_governance -v

# Run all tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

### Frontend (React dashboard in `frontend/dashboard/`)

```bash
npm install --prefix frontend/dashboard
npm run build --prefix frontend/dashboard   # builds static assets served by Python backend
npm run dev --prefix frontend/dashboard     # standalone dev server
npm run test --prefix frontend/dashboard    # Vitest
```

### Environment Variables

Required in `.env` or `src/.env`:

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Layer 1 parser, Layer 2 planner/replanner, Judge, web_search |
| `LAYER2_ACTOR_PROVIDER` | `bedrock` (default) or `openai` |
| AWS/Bedrock configuration | Default actor through the approved Nova 2 Lite EU inference profile |

## Architecture

### Oversight-centered Two-Layer System

**Layer 1 — Task Intake** (`src/layer1_intake/`): Parses raw user task text via OpenAI LLM into a canonical `IntakeResult` (Pydantic). Resolves an `ExecutionPolicy` from `configs/layer1_execution_policies.json` based on domain type and sensitivity. Its output bootstraps Layer 2.

**Layer 2 — Runtime Governance** (`src/layer2_runtime_governance/`): A LangGraph state machine that plans, acts, governs, and optionally pauses for human review.

### LangGraph Graph Topology

```
START → bootstrap_from_intake → planner → actor → { actor | governance }
governance → { actor | tool_gateway | replanner | human_review | external_execution | finalize_bootstrap }
human_review → { planner | actor | tool_gateway | replanner | governance | external_execution | finalize_bootstrap }
external_execution → { external_execution | human_review | finalize_bootstrap }
tool_gateway → observe_tool_result → actor
replanner → actor
finalize_bootstrap → END
```

### Central State

`TaskState` (canonical contracts under `domain/`, with `state.py` as a compatibility facade) is the single runtime source of truth. It holds intake, plan, actor/tool state, immutable completion candidates, observations, governance, HITL, and external execution records. Trace and dashboard artifacts are read-only projections.

### OPA-first governance and three active pillars

1. **Evidence Integrity & Provenance** — deterministic identity, uniqueness, concrete content, lineage, known-step, title-only fragility, and candidate/snapshot/selection digest binding. It never judges entailment.
2. **Trajectory Health** — deterministic progress deltas, replan basis, retry/recovery, unchanged/rejected resubmission, and revision churn. Replan stall and candidate resubmission remain distinct signals. Point confidence belongs to `base_facts.uncertainty`.
3. **Governed External Semantic Judge** — after OPA admission, evaluates the exact payload against the complete semantic observation base supplied as the deterministic, persisted `judge_evidence_bundle.v1`. It never searches the run, can cite only bundle-listed `evidence_id` values, excludes process narration, and emits only typed semantic assessments.
4. **OPA/Rego** — evaluates static policy, stages the Judge after deterministic gates, and returns the only final directive. Operational risk and behavioral intelligence remain shadow-only.

### Key Patterns

- **Typed planning/acting split**: Planner emits semantic `PlanStep` objects; actor executes one step at a time, emitting typed `ActorResult` with `ActorResultType` enum.
- **Tool Gateway** (`tools/`, compatibility facade `tool_gateway.py`): Registry-controlled, typed `ToolRequest` → `ToolExecutionResult` → minimal `ToolObservation`. Tools include `web_search`, `fetch_url_text`, and configured read-only MCP test tools.
- **Evidence tracking**: observations carry only stable identity, concrete content, and optional locator; provenance is reconstructed from immutable tool results. Task instructions are not observations.
- **HITL** (`hitl/`): LangGraph `interrupt`/`resume`. Review actions are reason-scoped; unsafe/uncertain Judge verdicts and unchanged/rejected candidates expose modify/replan/abort. Only an uncontaminated `capability_approval_required` gate can add exact-digest execution approval; unknown and legacy external pauses cannot.
- **External execution**: the connector is entered only after candidate/operation/Evidence/terminal-step preflight. Connector exceptions and mismatched or untyped receipts become checkpointable `indeterminate` reconciliation records.
- **Replanner** (`replanning.py`): Triggered on `NEEDS_REPLAN`; replaces only current + pending steps; completed steps are immutable history.
- **Persistence** (`persistence.py`): LangGraph checkpoints → `artifacts/runtime/checkpoints/`; per-thread artifacts → `artifacts/runtime/<thread_id>/`; behavioral SQLite → `artifacts/runtime/behavioral/behavioral.sqlite3`.
- **Traceability** (`observability/`, `evaluation_service/`): `layer2_trace.v6` is a JSON-exportable read model used for thesis evidence, HITL payloads, and offline evaluation.

### Key Files

| Path | Role |
|---|---|
| `AGENTS.md` | Authoritative operational contract for all contributors |
| `src/layer2_runtime_governance/domain/` | Canonical framework-independent runtime contracts |
| `src/layer2_runtime_governance/graph.py` | LangGraph wiring |
| `src/layer2_runtime_governance/runtime/` | Application services and thin graph adapters |
| `src/layer2_runtime_governance/governance_components/` | OPA input, staged admission, parsing, and fail-closed handling |
| `src/layer2_runtime_governance/signal_services/` | Evidence, Trajectory, Judge, and shadow fact producers |
| `configs/opa/layer2_governance/` | Sole final policy/directive logic |
| `src/layer1_intake/models.py` | Layer 1 Pydantic models |
| `architecture/architecturev3.md` | Current architecture reference doc |
