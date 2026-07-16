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
- `planning.py` and `replanning.py`: semantic planning capabilities.
- `acting.py`: small compatibility facade and production actor factory.
- `execution/`: actor prompts, provider-neutral action schemas, response normalization,
  result construction, evidence/claim guards, settings, and provider adapters.
- `tools/`: governed tool registry, validation, adapters, and content extraction.
- `governance.py`: checkpoint-only aliases for historical serialized governance types;
  new imports must target `governance_components/`.
- `signal_services/`: typed governance signal providers.
- `hitl.py`: typed human-review flow.
- `graph.py`: LangGraph composition and routing only.
- `runtime/`: lifecycle, planning, actor, tool, replanning, governance services, and
  thin LangGraph node adapters. The former `nodes.py` facade has been removed.
- `observability/`: trace models, read-only builders, compact views, and JSON
  serialization. The former `trace.py` facade has been removed.
- `evaluation.py` and `persistence.py`: evaluation and infrastructure surfaces.

## How to extend the runtime

1. Add or extend a typed domain contract.
2. Define the consuming port without importing a concrete provider.
3. Implement the adapter inside the owning capability or infrastructure package.
4. Wire the adapter in the composition root or graph builder.
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
- `test_layer2_architecture.py`: dependency direction and retired-surface checks.

Shared characterization fixtures live in `tests/layer2_runtime_fixtures.py`; production
code must never import them.

### Actor execution extension points

- Add provider-neutral runtime actions in `execution/action_schemas.py`.
- Normalize provider payloads only in `execution/response_parser.py`.
- Keep evidence sufficiency and claim-precision rules in `execution/evidence_guard.py`.
- Construct typed `ActorResult` values in `execution/result_builder.py`.
- Put SDK-specific calls in `execution/providers/`; providers must return the same
  runtime action envelope and must not import graph nodes or governance policy code.
