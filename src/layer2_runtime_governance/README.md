# Layer 2 Runtime and Governance

Layer 2 is a modular runtime for governed plan execution. LangGraph coordinates the flow, while capability packages own domain rules and infrastructure adapters implement external effects.

## Runtime flow

```text
bootstrap -> planning -> execution -> governance
                                  -> tool gateway -> observation -> execution
                                  -> replanning -> execution
                                  -> human review
                                  -> finalization
```

## Dependency direction

```text
domain contracts <- capability services <- adapters/runtime <- composition root
```

Domain modules must remain independent from LangGraph, provider SDKs, HTTP, subprocesses, and persistence. The runtime graph applies typed results; it does not own provider or policy business logic.

## Capability map

- `state.py`: compatibility facade for canonical contracts owned by `domain/`.
- `planning/`: semantic planning and replanning capabilities; `replanning.py` is a
  compatibility facade for the previous import path.
- `acting.py`: small compatibility facade and production actor factory.
- `execution/`: actor prompts, provider-neutral action schemas, response normalization,
  result construction, evidence/claim guards, settings, and provider adapters.
- `domain/evidence_policy.py`: canonical support-level and claim-precision
  classification shared by execution guards and governance compatibility surfaces.
- `tools/`: governed tool registry, validation, adapters, and content extraction.
- `governance.py`: checkpoint-only aliases for historical serialized governance types;
  new imports must target `governance_components/`.
- `signal_services/`: typed governance signal providers.
- `hitl/`: typed human-review flow and dashboard projections; `hitl_dashboard.py`
  remains a compatibility facade.
- `graph.py`: LangGraph composition and routing only.
- `runtime/`: lifecycle, planning, actor, tool, replanning, governance services, and
  thin LangGraph node adapters. The former `nodes.py` facade has been removed.
- `observability/`: trace models, read-only builders, compact views, and JSON
  serialization. The former `trace.py` facade has been removed.
- `evaluation/`: offline evaluation over exported traces.
- `behavioral/`, `judge/`, and `oversight/`: typed behavioural memory, selective
  semantic judge, and deterministic dynamic governance signals.
- `governance_components/`: OPA contracts, input-document construction, validation,
  response parsing, and fail-closed handling.
- `persistence.py`: compatibility facade for `infrastructure/runtime_artifacts.py`.
- `infrastructure/`: official checkpoint composition, bounded retention, environment
  loading, runtime artifacts, and dependency-warning filters.
- `bedrock_client.py`: compatibility facade for the Bedrock transport under
  `execution/providers/`.

## Persistence and interrupts

LangGraph checkpoints are the only resumable memory for `interrupt()` / `Command(resume=...)`.
Trace JSON, dashboard snapshots, and the behavioural database are read models and must never
be used to reconstruct an interrupted graph.

- Local development defaults to the official `SqliteSaver` at
  `artifacts/runtime/checkpoints/layer2_checkpoints.sqlite3`.
- Deployed multi-worker environments can select the official `PostgresSaver` with
  `LAYER2_CHECKPOINT_BACKEND=postgres` and `LAYER2_CHECKPOINT_POSTGRES_URI`.
- PostgreSQL extras are isolated in `requirements-production.txt`.
- `LAYER2_COMPLETED_THREAD_RETENTION_DAYS` bounds terminal checkpoint retention; pending
  human reviews and non-terminal threads are never pruned by the maintenance pass.
- Audit artifacts remain available after terminal checkpoint pruning.

The LangGraph store API is intentionally not used as a substitute for checkpoints: stores
provide cross-thread application memory, while checkpointers persist graph execution state.

## How to extend the runtime

1. Add or extend a typed domain contract.
2. Define the consuming port without importing a concrete provider.
3. Implement the adapter inside the owning capability or infrastructure package.
4. Wire the adapter through `compose_layer2_runtime`; CLI and dashboard entrypoints
   both use this composition root.
5. Add capability-level tests before graph-level integration tests.
6. Update this map if the extension creates a new architectural boundary.

Only checkpoint-sensitive legacy modules remain import-compatible. New code must target
the capability package; removed facades must not be recreated.

## Test map

- `test_layer2_core_runtime.py`: lifecycle and end-to-end runtime invariants.
- `test_layer2_planning_runtime.py`: planner and replanner behavior.
- `test_layer2_actor_runtime.py`: actor contracts, providers, and evidence guards.
- `test_layer2_tool_runtime.py`: gateway execution and observation.
- `test_layer2_governance_runtime.py`: OPA-first enforcement and policy signals.
- `test_layer2_hitl_runtime.py`: interrupt, review, edit, and resume behavior.
- `test_layer2_checkpointing.py`: official saver selection and fail-safe retention.
- `test_layer2_architecture.py`: dependency direction and retired-surface checks.

Shared characterization fixtures live in `tests/layer2_runtime_fixtures.py`; production
code must never import them.

### Actor execution extension points

- Add provider-neutral runtime actions in `execution/action_schemas.py`.
- Normalize provider payloads only in `execution/response_parser.py`.
- Keep canonical evidence sufficiency and claim-precision classification in
  `domain/evidence_policy.py`; execution-specific guards remain in
  `execution/evidence_guard.py`.
- Construct typed `ActorResult` values in `execution/result_builder.py`.
- Put SDK-specific calls in `execution/providers/`; providers must return the same
  runtime action envelope and must not import graph nodes or governance policy code.
