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
  - `LAYER2_ACTOR_BEDROCK_THINKING=off|adaptive` (default `off`; adaptive currently restricted to Claude Sonnet 4.6)
  - `LAYER2_ACTOR_BEDROCK_THINKING_EFFORT=low|medium|high|max` (default `high`, effective only in adaptive mode)
  - Actor thinking metadata crosses the provider boundary as plain strings. The checkpoint serializer retains exact legacy allowlist entries for `BedrockThinkingMode` and `BedrockThinkingEffort` because the first thinking-enabled runs persisted these enums; no provider-wide allowlist is permitted.
  - Adaptive thinking uses Converse `additionalModelRequestFields` and `toolChoice=auto`; the runtime still requires typed tool actions. Thinking blocks are discarded. Each turn is a fresh request built from canonical state, not an API conversation replay.
  - Optional v6 actor fields `actor_model_id`, `actor_thinking_mode`, and `actor_thinking_effort` preserve experiment configuration and contribute to Layer 5 fingerprints when known. Historical absence remains unknown. New thinking experiments require fresh campaign/thread IDs; see `docs/evaluation/actor_thinking.md`.
  - `AWS_REGION=eu-central-1`
  - `AWS_SESSION_TOKEN=` when temporary AWS credentials are used
