# Governance and HITL Contract

This document defines the implemented Layer 2 governance boundary. The LangGraph
`governance` node is the Policy Enforcement Point (PEP); OPA/Rego is the only
Policy Decision Point (PDP) and the sole authority for the final directive.
Python collects typed facts, invokes bounded services, validates OPA output, and
applies the returned directive. It does not run a second decision combiner.

## External operation boundary

Every runtime action is classified by runtime-owned normalizers and the Tool
Gateway registry:

| Boundary | Meaning | Judge preflight |
| --- | --- | --- |
| `local` | No operation crosses the runtime boundary | Not required |
| `evidence_read` | Public/read-only evidence acquisition, including `web_search` and `fetch_url_text` | Not required |
| `governed_external` | Enterprise reads, communication, mutation, publication, or an unknown external capability | Required for an execution candidate unless static policy already hard-denied it |

The canonical `ExternalOperationProposal` is immutable and contains operation
identity, evaluated plan step, capability, boundary, family, normalized effect,
typed target, bounded/redacted preview, deterministic payload digest, capability
profile version, and fact provenance. Compatibility fields `capability_mode` and
`external_impact` are derived projections only.

Payload families are deliberately small:

- communication: channel, recipient, subject, body, destination class;
- database: original and normalized SQL, AST-derived statement class, statement
  count, validity, ambiguity, and database profile;
- generic: bounded action, target, and typed string fields.

SQL normalization uses `sqlglot`, accepts one statement, and classifies comments,
casing, CTEs, mutations, unknown commands, and multiple statements
deterministically. Email normalization never judges tone or harmful intent.

The final external-communication actor turn exposes only the typed draft completion
action (`channel`, `recipient`, `subject`, `body`), not generic final-output actions.
Runtime planning normalizes a local communication task to one draft step, or to one
evidence step plus one draft step when stronger evidence is genuinely required.
This keeps semantic assessment in the judge and prevents planner/actor meta-steps
from pre-empting policy.

## Pillar authority

| Component | Mode | Decision influence | Failure semantics |
| --- | --- | --- | --- |
| Static policy | active | Hard deterministic guardrails | Hard deny is absorbing |
| Evidence | active | Structural integrity, provenance, and snapshot binding | Required failure goes fail-closed through OPA |
| Trajectory | active | Recovery, replan, repeated revision/provider escalation | Required failure goes fail-closed through OPA |
| Semantic judge | active when OPA requires it | OPA maps semantic assessment to a directive | Required unavailable/invalid/digest mismatch goes to HITL |
| Operational risk | shadow | None | Error is traced; no fail-closed |
| Behavioral | shadow | None | Error is traced; no fail-closed |

Static policy blocks only configured deterministic violations. Externality or a
write compatibility flag alone does not cause deny or HITL. Current hard
violations include:

- `read_only_mode` plus a mutating/unknown execution-candidate effect;
- a non-`SELECT`, ambiguous, or multiple SQL statement under the
  `enterprise_db.read_only` profile;
- an explicitly prohibited destination or recipient domain.

Operational risk is a shadow consequence assessment. It records applicability,
recoverability, concrete compensation mechanism when known, blast radius,
sensitivity, exposure, target criticality, impact level, dominant factors,
unknown facts, fact provenance, and profile version. It does not use the former
weighted external/write/review score.

Behavioral continues to persist SQLite history and export similarity, deviation,
novelty, flags, and counterfactual recommendation. Its mode is `shadow` and
`decision_influence=false`. The runtime reuses the assessment during a run and
persists the read model at stable audit boundaries.

## OPA preflight, staged admission, and final matrix

OPA performs a bounded staged evaluation:

1. `data.layer2.governance.required_pillars` selects deterministic active signals;
2. a preliminary `data.layer2.governance.decision` evaluates static policy,
   Evidence, and Trajectory and returns typed `judge_admission`;
3. only an admitted governed-external candidate invokes the Judge;
4. a final `data.layer2.governance.decision` maps all available signals to the
   runtime directive.

Judge admission is owned only by Rego:

```text
execution_candidate
AND boundary == governed_external
AND NOT static_policy.hard_deny
AND Evidence has no structural gap
AND Trajectory is neither stalled nor requesting replan
AND required deterministic pillars are available
AND policy versions and payload digest are valid
```

The final deterministic precedence is:

| Condition | Directive |
| --- | --- |
| Policy version mismatch | `wait_human_review` |
| Static hard violation | `stop` / deny |
| Required judge unavailable, invalid, non-invoked, or wrong digest | `wait_human_review` |
| Required active evidence/trajectory unavailable | `wait_human_review` |
| Explicit clarification | `ask_user_clarification` |
| Replan request repeated without a new observable basis | `wait_human_review` |
| Judge `unsafe` or `uncertain` | `wait_human_review`, never hard deny |
| Judge `needs_revision` | `revise_action` |
| Evidence gap | `request_more_evidence` |
| Admissible explicit actor replan request | `replan` |
| Read-only envelope | `continue_read_only` |
| Active checks satisfied, including judge `safe` when required | `continue` |

`revise_action` returns audit-safe finding codes to the actor, invalidates the
current proposal and approval, increments the per-run revision counter, and
returns to `ACTING`. Repeated revisions are counted only at this external-action
boundary and may eventually require human review.

## Semantic judge contract

OPA, not Python, decides when invocation is required. `JudgeContext` includes the
authoritative task goal, constraints, context, success criteria and execution
intent; the complete non-authoritative semantic plan; completed-step summaries;
every canonical observation as a bounded typed projection; actor-selected
references as attribution markers; same-operation revision findings; target and
the exact digest-bound proposal.

It excludes scratchpad, raw tool arguments/history, ordered runtime events,
turn/tool/replan/retry counters, point confidence, recovery history, behavioral,
risk, and other process narration. Boundedness limits representation size, never
silently the evidence universe. Truncation or omission is explicit and forces
`context_incomplete`/`uncertain` where support cannot be determined.

The judge returns:

- status: `active`, `not_applicable`, `unavailable`, or `invalid`;
- verdict: `safe`, `needs_revision`, `unsafe`, or `uncertain`;
- severity, confidence, typed findings, summary, concise rationale;
- evaluated operation, evidence-snapshot, and evidence-selection digests plus
  provider/model/prompt metadata.

Verdict, severity, and finding categories are closed enums in provider structured
output. A free-form category is invalid; it is never coerced into a nearby label.
The digest field accepts exactly the lowercase SHA-256 representation used by the
immutable proposal.

The judge cannot choose runtime directives. Confidence is assessment confidence,
not probability of harm and is never multiplied by an operational-risk score.

## Uniform HITL and digest integrity

HITL actions are reason-scoped. In particular:

- Judge `unsafe`/`uncertain` or exhausted revisions: `modify_and_continue`,
  `replan`, `abort`;
- capability approval: `approve_continue`, `modify_and_continue`, `abort`;
- clarification: `ask_clarification`, `abort`;
- indeterminate receipt reconciliation: `approve_continue`,
  `request_more_evidence`, `abort`.

Legacy requests without a reason-scoped contract retain the broad compatibility
set. `modify_and_continue` always requires a typed valid edit. A review request
includes operation id, digest, proposal summary, target/destination, Judge
findings, and the proposal where applicable.

Approval is bound to the exact current digest. Modification creates a new
immutable proposal and digest, atomically rebinds the pending completion candidate
and its Evidence selection digest, invalidates the earlier approval and Judge
assessment, clears any executable tool request, and enters `READY_GOVERNANCE`.
It never routes directly to Tool Gateway or an external executor.
`approve_continue_read_only` rejects an unchanged mutating proposal.

LangGraph `interrupt()` and `Command(resume=...)` remain the only pause/resume
mechanism. Official LangGraph checkpoints are authoritative; trace, dashboard
artifacts, and behavioral SQLite are read models.

## Execution intent and external execution

`ExecutionIntent` is canonical in `TaskSpec` and projected into `TaskState`. Its
values are `proposal_only` and `execute_after_governance`; old payloads and
checkpoints recover as `proposal_only`. Intent records the requested outcome but
never grants authority.

For `proposal_only`, approval is terminal: the phase is `approved_draft`, no
execution request is created, and the UI must not say `approved_for_execution`.
For `execute_after_governance`, exact OPA continue or HITL approval creates
`ExternalExecutionRequest` and enters `READY_EXTERNAL_EXECUTION`.

The `external_execution` service checks proposal, execution candidate,
authorization source, exact digest, registry entry, server/tool allowlist,
read-only envelope, idempotency key, and prior receipt before calling
`ExternalOperationExecutorPort`. It cannot change the payload or decide policy.

Receipts distinguish `executed`, `rejected_before_execution`, `failed`, and
`indeterminate`. Only deterministic retryable failure may receive one configured
retry with a new stable attempt scope. `indeterminate` prohibits automatic retry
and opens HITL reconciliation.

`MCPExternalOperationExecutor` is selected whenever configuration enables a real
capability. `DeferredExternalOperationExecutor` remains fail-closed only. MCP is
a transport adapter, never policy or authorization.

## Observability

`layer2_trace.v6` has a closed `external_execution` section with intent, request,
receipt history, digest, connector metadata, external reference, and attempt count.
Actor/HITL fields expose the current proposal and digest; governance pillar results expose `active`,
`shadow`, `not_applicable`, `unavailable`, and `decision_influence`. Historical
normalization remains confined to the offline evaluation boundary.

Primary implementation locations:

- `src/layer2_runtime_governance/external_operations/`
- `src/layer2_runtime_governance/governance_components/`
- `src/layer2_runtime_governance/judge/`
- `src/layer2_runtime_governance/signal_services/`
- `src/layer2_runtime_governance/runtime/governance_service.py`
- `src/layer2_runtime_governance/hitl/`
- `configs/opa/layer2_governance/`
# Boundary HITL contestuale

Le azioni esposte dal review payload dipendono dal motivo della pausa. Un
chiarimento consente chiarimento o abort; un approval gate consente approve,
edit o reject; un finding Judge non risolvibile propone revisione/replan/abort;
la riconciliazione di una receipt indeterminata espone solo le scelte pertinenti.
Il digest resta vincolante: ogni edit genera una proposta rinormalizzata e passa
nuovamente da Judge e OPA.
