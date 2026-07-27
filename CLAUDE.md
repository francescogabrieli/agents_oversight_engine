# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Context

This is a Master's Thesis proof-of-concept for a **governed AI agent architecture** with human oversight. It demonstrates controlled task intake, runtime planning/execution, multi-layer governance enforcement, and Human-in-the-Loop (HITL) review. The primary reference is `AGENTS.md` — treat it as the authoritative operational contract.

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
| `OPENAI_API_KEY` | Layer 1 parser, Layer 2 planner/replanner, web_search tool |
| `OPENROUTER_API_KEY` | Layer 2 actor |
| `LAYER2_ACTOR_PROVIDER` | `openrouter` (default) or `openai` |
| `LAYER2_ACTOR_OPENROUTER_MODEL` | Default: `openai/gpt-oss-120b:free` |

## Architecture

### Two-Layer System

**Layer 1 — Task Intake** (`src/layer1_intake/`): Parses raw user task text via OpenAI LLM into a canonical `IntakeResult` (Pydantic). Resolves an `ExecutionPolicy` from `configs/layer1_execution_policies.json` based on domain type and sensitivity. Its output bootstraps Layer 2.

**Layer 2 — Runtime Governance** (`src/layer2_runtime_governance/`): A LangGraph state machine that plans, acts, governs, and optionally pauses for human review.

### LangGraph Graph Topology

```
START → bootstrap_from_intake → planner → actor → governance
governance → { tool_gateway | replanner | human_review | finalize_bootstrap | actor }
human_review → { planner | actor | tool_gateway | replanner | finalize_bootstrap }
tool_gateway → observe_tool_result → actor
replanner → actor
finalize_bootstrap → END
```

### Central State

`TaskState` (dataclass in `state.py`) is the single source of truth passed through every node — it holds intake payload, plan steps, actor result, tool loop state, governance history, HITL state, evidence, scratchpad, and trace data. Every node receives a deepcopy and returns an updated dict.

### Five-Layer Governance (decision_combiner.py integrates all)

1. **Static policy** (`governance.py`) — OPA HTTP engine; hard denials (`DENY`) are non-negotiable
2. **Deterministic dynamic policy** (`oversight.py`) — scores evidence quality, operational risk, and trajectory health against thresholds in `configs/layer2_oversight_policy.json`
3. **Behavioral intelligence** (`behavioral.py`) — SQLite historical read model scoring similarity/deviation/novelty
4. **Selective LLM judge** (`judge.py`) — invoked only for fragile outputs or risky drafts
5. **Decision combiner** (`decision_combiner.py`) — Weighted Minimal Safe Intervention; outputs `GovernanceDirective` (`CONTINUE`, `DENY`, `WAIT_HUMAN_REVIEW`, etc.)

### Key Patterns

- **Typed planning/acting split**: Planner emits semantic `PlanStep` objects; actor executes one step at a time, emitting typed `ActorResult` with `ActorResultType` enum.
- **Tool Gateway** (`tool_gateway.py`): Registry-controlled, typed `ToolRequest` → `ToolExecutionResult` → `ObservedEvidence`. Tools: `web_search`, `fetch_url_text`, and four MCP local test tools.
- **Evidence tracking**: `ObservedEvidence` carries orthogonal content-state, authority, document-type, extractability, citation-alignment, and provenance attributes. Task-provided facts are materialized into initial observed evidence at bootstrap.
- **HITL** (`hitl.py`): LangGraph `interrupt`/`resume`. `WAITING_HUMAN_REVIEW` pauses the graph; typed `HumanReviewRequest` is persisted; decisions are typed (`APPROVE_CONTINUE`, `MODIFY_AND_CONTINUE`, `ABORT`, etc.).
- **Replanner** (`replanning.py`): Triggered on `NEEDS_REPLAN`; replaces only current + pending steps; completed steps are immutable history.
- **Persistence** (`persistence.py`): LangGraph checkpoints → `artifacts/runtime/checkpoints/`; per-thread artifacts → `artifacts/runtime/<thread_id>/`; behavioral SQLite → `artifacts/runtime/behavioral/behavioral.sqlite3`.
- **Traceability** (`trace.py`, `evaluation.py`): `Layer2RuntimeTrace` is a JSON-exportable snapshot used for thesis evidence, HITL payloads, and offline evaluation.

### Key Files

| Path | Role |
|---|---|
| `AGENTS.md` | Authoritative operational contract for all contributors |
| `src/layer2_runtime_governance/state.py` | Central `TaskState` definition |
| `src/layer2_runtime_governance/graph.py` | LangGraph wiring |
| `src/layer2_runtime_governance/nodes.py` | All graph node implementations |
| `src/layer2_runtime_governance/governance.py` | Static policy, OPA integration |
| `src/layer2_runtime_governance/decision_combiner.py` | Weighted Minimal Safe Intervention combiner |
| `src/layer2_runtime_governance/oversight.py` | Deterministic dynamic policy scoring |
| `src/layer1_intake/models.py` | Layer 1 Pydantic models |
| `configs/layer2_oversight_policy.json` | Dynamic policy thresholds/weights |
| `architecture/architecturev3.md` | Current architecture reference doc |
