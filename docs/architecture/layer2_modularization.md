# Layer 2 modularization and scaling roadmap

## Goal

Layer 2 remains a modular monolith by default. Capability boundaries are enforced in code so a capability can later become a service only when it has an independent lifecycle, scaling profile, security boundary, ownership boundary, or failure policy.

## Reading path

1. `src/layer2_runtime_governance/README.md`
2. `graph.py` for topology
3. `runtime/routing.py` for branch decisions
4. `runtime/node_adapters.py` for LangGraph-to-capability adaptation
5. the owning capability package
6. infrastructure adapters only when debugging I/O

## Current capability boundaries

- `domain`: canonical framework-independent planning, execution-state, review,
  lifecycle, serialization, evidence, and `TaskState` contracts.
- `runtime`: lifecycle, planning, actor, tool, replanning, and governance services,
  plus graph contracts, routing, node adapters, and thread context.
- `tools`: registry, security policy, backend adapters, and extractors.
- `execution`: provider-neutral action schemas, prompts, response parsing, typed result
  construction, evidence guards, provider settings, and concrete provider adapters.
- `governance_components`: policy contracts, OPA input, HTTP adapter, OPA-first
  orchestration, directive validation, and fail-closed behavior. OPA/Rego owns the final
  decision; no parallel Python decision combiner remains.
- `observability`: immutable trace models, trace builders, compact views, and JSON serialization.
- `infrastructure`: checkpoint composition, persistence ports, and local adapters.
- external OPA: independently deployable Policy Decision Point.

The `state.py` and `governance.py` aliases remain only for historical checkpoint
deserialization. The former `nodes.py`, `trace.py`, and Python decision-combiner surfaces
have been removed. New responsibilities must enter the owning capability package.

## Capability-oriented tests

The former runtime test monolith is split into core, planning, actor, tool, governance,
and HITL modules. `tests/layer2_runtime_fixtures.py` contains shared characterization
fixtures, while `tests/test_layer2_architecture.py` enforces dependency direction and
prevents retired facades from returning.

## Horizontal scaling prerequisites

The runtime cannot be horizontally scaled safely while canonical checkpoints and behavioral history are process-local or stored in a worker-local SQLite file. Production scaling requires:

1. stateless LangGraph workers;
2. a shared durable checkpointer adapter, normally PostgreSQL;
3. a shared artifact adapter, normally object storage plus indexed metadata;
4. idempotency keys for tool execution and HITL resume;
5. bounded timeouts and structured retry policy for every remote adapter;
6. immutable policy bundle versions;
7. correlation identifiers propagated through runtime, policy, tools, and traces.

SQLite and local disk remain valid development adapters behind ports. They are not the production scaling model.

## Extraction criteria

OPA is already a service because policy has a separate security and deployment boundary. The Tool Gateway should become a service only when credentials, network egress, throughput, or multiple runtime consumers require independent operation. Deterministic in-process signal calculations should remain modules unless evidence demonstrates a separate scaling or failure domain.
