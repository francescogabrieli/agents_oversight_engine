# Layer 2 Runtime and Governance

Layer 2 is the live LangGraph runtime and governance boundary for governed plan execution. Layer 3 owns capability execution and Layer 4 owns persistence, evidence, and observability; Layer 2 coordinates them through typed ports.

## Runtime flow

```text
bootstrap -> planning -> execution -> governance
                                  -> tool gateway -> observation -> execution
                                  -> replanning -> execution
                                  -> human review
                                  -> payload revision -> governance
                                  -> finalization
```

## Dependency direction

```text
domain contracts <- capability services <- adapters/runtime <- composition root
```

Domain modules must remain independent from LangGraph, provider SDKs, HTTP, subprocesses, and persistence. The runtime graph applies typed results; it does not own provider or policy business logic.

## Capability map

- `state.py`, `governance.py`, and `replanning.py`: checkpoint compatibility anchors.
  LangGraph may need their historical qualified type paths while restoring durable
  checkpoints; new code must import from `domain/`, `governance_components/`, and
  `planning.replanning` directly.
- `planning/`: semantic planning and replanning capabilities.
- `execution/`: actor prompts, provider-neutral action schemas, response normalization,
  result construction, settings, the actor factory, and provider adapters. The Bedrock
  actor adapter lives in `execution/providers/bedrock.py`; its Converse client and
  settings live in `execution/providers/bedrock_runtime.py`. Real runs default to the
  OpenAI Responses API adapter in `execution/providers/openai.py` with `gpt-6-luna`.
- `domain/evidence.py`: observation lineage plus digest-bound evidence snapshot and
  selection projections used at governance boundaries.
- `layer3_tool_bus_external_capabilities/tool_contract.py`: canonical read-only tool
  requests, results, adapter-created observations, and the Tool Gateway port.
- `layer3_tool_bus_external_capabilities/tools/`: governed tool registry, validation,
  adapters, and content extraction.
- `layer3_tool_bus_external_capabilities/external_operations/`: immutable external
  proposals, runtime/registry boundary projection, AST-backed SQL and structural
  communication normalization, capability consequence profiles, and the production
  executor seam.
- `signal_services/`: typed governance signal providers.
- `hitl/`: typed human-review flow; dashboard projections live in `hitl/dashboard.py`.
- `graph.py`: LangGraph composition and routing only.
- `runtime/`: lifecycle, planning, actor, tool, replanning, governance services, and
  thin LangGraph node adapters. The former `nodes.py` facade has been removed.
- `judge/`: governed-external semantic assessment used by the Layer 2 governance
  boundary.
- `layer4_state_evidence_observability/observability/`: trace models, read-only
  builders, compact views, and JSON serialization.
- `layer4_state_evidence_observability/behavioral/` and `oversight/`: shadow-only
  behavioural memory and persisted oversight assessment contracts.
- `governance_components/`: OPA contracts, input-document construction, validation,
  response parsing, and fail-closed handling.
- `layer4_state_evidence_observability/infrastructure/`: official checkpoint
  composition, bounded retention, environment loading, review-decision transport,
  and the runtime artifact store in `infrastructure/runtime_artifacts.py`. Runtime
  warning filters remain in `runtime_warnings.py` because they belong to Layer 2
  startup.

## Persistence and interrupts

LangGraph checkpoints are the only resumable memory for `interrupt()` / `Command(resume=...)`.
Trace JSON, dashboard snapshots, and the behavioural database are read models and must never
be used to reconstruct an interrupted graph.

The standalone dashboard validates and writes typed, request-bound decisions to a durable
decision inbox. It never invokes the graph. The original leased runtime owner claims each
decision and resumes the official checkpoint in the same process; the inbox records delivery
state but is not resumable workflow memory.

The runtime heartbeats its lease during resume and supports explicit interrupted-thread
recovery through `main_layer2.py --attach-thread <thread_id>`. Recovery is permitted only
for the exact request still present in the canonical checkpoint. The bundled inbox adapter
is local SQLite; multi-host composition requires a shared implementation of its port.

## Governance authority

OPA/Rego is the sole directive authority. Static policy contains only deterministic
hard guardrails; externality and write compatibility fields do not imply deny or HITL.
Evidence and trajectory are active. Operational risk and behavioral are exported with
`mode=shadow` and `decision_influence=false`. OPA requires the semantic judge before
every non-denied `governed_external` execution candidate, but not for local work,
partial drafts, `web_search`, or `fetch_url_text`.

The judge returns typed semantic verdicts and findings, never runtime directives.
`needs_revision` maps in OPA to `revise_action`; the runtime invalidates that payload
and returns audit-safe feedback to the actor. HITL approvals are bound to the exact
payload digest, and every modification returns to governance with a new digest.
The only productive side-effect path is the capability-bound MCP external executor.
Email and database execution are disabled by default and require explicit connector
configuration.

External communication synthesis uses a dedicated actor action carrying `channel`,
`recipient`, `subject`, and `body`; generic completion actions are not exposed on
that turn. The runtime derives policy facts and the immutable operation digest from
this typed draft. Planning services preserve the planner-owned semantic steps
without compaction, insertion, or lexical rewriting; the planner remains responsible
for the evidence-acquisition and drafting trajectory.

## Governed external execution

The graph now includes:

```text
governance -> external_execution -> {external_execution | human_review | finalize}
human_review -> external_execution
```

The self-edge is only a bounded retry of deterministic `failed + retryable`; registry
configuration caps attempts at two. `indeterminate` opens typed reconciliation and
cannot use that edge.

Layer 1 owns `ExecutionIntent` (`proposal_only` or
`execute_after_governance`). Proposal-only approval ends as `approved_draft`, never
`approved_for_execution`. Executable intent still needs the exact OPA/HITL digest,
an enabled capability, allowlisted MCP server/tool, the read-only envelope, and a
stable idempotency key.

The external registry is separate from the actor-visible Tool Gateway registry. This
keeps mutating MCP tools out of actor tool binding; the external executor may invoke
only the one exact MCP tool mapped to the canonical capability.

- Local development defaults to the official `SqliteSaver` at
  `artifacts/runtime/checkpoints/layer2_checkpoints.sqlite3`.
- Deployed multi-worker environments can select the official `PostgresSaver` with
  `LAYER2_CHECKPOINT_BACKEND=postgres` and `LAYER2_CHECKPOINT_POSTGRES_URI`.
- PostgreSQL extras are isolated in `requirements-production.txt`.
- `LAYER2_COMPLETED_THREAD_RETENTION_DAYS` bounds terminal checkpoint retention and
  defaults to `0`, because completed/aborted runs remain available through immutable
  runtime artifacts while only active or interrupted runs require resumable checkpoints; pending
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

Only checkpoint-compatible compatibility facades remain at the package root. Production
code must target the capability package; retired non-persistent facades must not be
recreated at the root.

## Test map

- `test_layer2_core_runtime.py`: lifecycle and end-to-end runtime invariants.
- `test_layer2_planning_runtime.py`: planner and replanner behavior.
- `test_layer2_actor_runtime.py`: actor contracts, providers, and evidence guards.
- `test_layer2_tool_runtime.py`: gateway execution and observation.
- `test_layer2_governance_runtime.py`: OPA-first enforcement and policy signals.
- `test_layer2_external_operations.py`: external proposal normalization, AST SQL,
  judge preflight, shadow isolation, revision, and digest integrity.
- `test_layer2_external_execution.py`: intent, preflight, receipts, graph routing,
  HITL, modification, deny, bounded retry, and duplicate suppression.
- `test_external_execution_mcp.py`: SDK response validation, timeout, allowlist,
  lifecycle, local SMTP capture, and temporary SQLite integration.
- `test_layer2_hitl_runtime.py`: interrupt, review, edit, and resume behavior.
- `test_layer2_checkpointing.py`: official saver selection and fail-safe retention.
- `test_layer2_architecture.py`: dependency direction and retired-surface checks.

Shared characterization fixtures live in `tests/layer2_runtime_fixtures.py`; production
code must never import them.

### Actor execution extension points

- Add provider-neutral runtime actions in `execution/action_schemas.py`.
- Normalize provider payloads only in `execution/response_parser.py`.
- Keep observation lineage and evidence-selection digests in `domain/evidence.py`;
  validate actor-selected observation identifiers in `execution/result_builder.py`.
- Construct typed `ActorResult` values in `execution/result_builder.py`.
- Put SDK-specific calls in `execution/providers/`; providers must return the same
  runtime action envelope and must not import graph nodes or governance policy code.
