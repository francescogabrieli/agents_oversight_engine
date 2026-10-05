# Operations, debugging and environment

This document is a required reference under [AGENTS.md](../../AGENTS.md).
Read it when the routing table there applies. It preserves the detailed sections
extracted from that file; these are maintained instructions, not a historical archive.
All code paths and commands below are relative to the repository root.

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

Export the current Layer 2 graph without executing planner, actor, tools, or external capabilities:

```bash
.venv/bin/python scripts/export_runtime_graph.py --render
```

The command writes the exact LangGraph topology to `figures/layer2_runtime_graph.mmd`
and `figures/layer2_runtime_graph.png`. It also writes a thesis-oriented presentation
projection to `figures/layer2_runtime_graph_thesis.mmd` and
`figures/layer2_runtime_graph_thesis.png`. The projection is derived from the same
compiled graph, keeps the native vertical structure and a reduced set of actual
conditional routes, and leaves the complete routing available in the canonical export.
`--render` uses Mermaid CLI locally; it does not send the graph to mermaid.ink.

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

Run only the preregistered live Semantic Judge probes OP10–OP13 after verifying the
loaded OPA bundle. The actor is controlled for these cases; the configured Judge
provider is live. Use a fresh campaign ID and do not count dry-runs as results:

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_opa_bundle.py
.\.venv\Scripts\python.exe .\scripts\run_oversight_demo_pool.py --campaign-id oversight_demo_judge_v8 --mode live --pillar judge
```

See [Judge demo design](../evaluation/oversight_demo_judge_extension.md). Without
`--pillar judge`, `--mode live` selects OP05–OP13, including the existing controls.


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
- OpenAI Responses API for the default Layer 2 actor backend using `gpt-6-luna`
- OpenAI API for the default Layer 2 replanner
- OpenAI API for the default Layer 2 semantic judge
- OpenAI API for the `web_search` path in the default Layer 2 tool gateway
- Python standard library HTTP/HTML extraction for the `fetch_url_text` path in the default Layer 2 tool gateway
- LangGraph for Layer 2 runtime orchestration
- durable local SQLite LangGraph checkpoint persistence for the current local runtime graph

Environment variables may be loaded from:

- `.env`
- `src/.env`

Expected secret:

- `OPENAI_API_KEY`

Expected Layer 2 actor configuration:

- `LAYER2_ACTOR_PROVIDER=openai` (default)
- `LAYER2_ACTOR_OPENAI_MODEL=gpt-6-luna` (default)
- `LAYER2_ACTOR_OPENAI_TIMEOUT_SECONDS=30` (default)
- The actor reads `OPENAI_API_KEY` from the process environment or the existing `.env` / `src/.env` file.

AWS credentials and `LAYER2_ACTOR_BEDROCK_*` settings are only needed when explicitly
selecting the historical Bedrock comparison adapter. Optional v6 actor fields
`actor_model_id`, `actor_thinking_mode`, and `actor_thinking_effort` preserve known
experiment configuration; missing historical values remain unknown. See
`docs/evaluation/actor_thinking.md` for that Bedrock-only experiment reference.
