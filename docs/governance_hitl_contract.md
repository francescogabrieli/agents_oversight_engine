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
| Evidence | active | Evidence gap and precision support | Required failure goes fail-closed through OPA |
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

## OPA preflight and final matrix

OPA performs two calls:

1. `data.layer2.governance.required_pillars`;
2. `data.layer2.governance.decision`.

The mandatory judge condition is owned only by Rego:

```text
execution_candidate
AND boundary == governed_external
AND NOT static_policy.hard_deny
```

The final deterministic precedence is:

| Condition | Directive |
| --- | --- |
| Policy version mismatch | `wait_human_review` |
| Static hard violation | `stop` / deny |
| Required judge unavailable, invalid, non-invoked, or wrong digest | `wait_human_review` |
| Required active evidence/trajectory unavailable | `wait_human_review` |
| Explicit clarification | `ask_user_clarification` |
| Trajectory stop/repeated provider failure/repeated revision | `stop` or `wait_human_review` according to Rego |
| Judge `unsafe` or `uncertain` | `wait_human_review`, never hard deny |
| Judge `needs_revision` | `revise_action` |
| Evidence gap | `request_more_evidence` |
| Trajectory degradation | `replan` |
| Read-only envelope | `continue_read_only` |
| Active checks satisfied, including judge `safe` when required | `continue` |

`revise_action` returns audit-safe finding codes to the actor, invalidates the
current proposal and approval, increments the per-run revision counter, and
returns to `ACTING`. Repeated revisions contribute to trajectory health.

## Semantic judge contract

OPA, not Python, decides when invocation is required. `JudgeContext` includes
only the authorized task goal/constraints/context, the step identified by
`actor_result.plan_step_id`, the exact proposal and digest, relevant evidence
and support levels, target/destination facts, and invocation reason.

The judge returns:

- status: `active`, `not_applicable`, `unavailable`, or `invalid`;
- verdict: `safe`, `needs_revision`, `unsafe`, or `uncertain`;
- severity, confidence, typed findings, summary, concise rationale;
- evaluated operation digest and provider/model/prompt metadata.

Verdict, severity, and finding categories are closed enums in provider structured
output. A free-form category is invalid; it is never coerced into a nearby label.
The digest field accepts exactly the lowercase SHA-256 representation used by the
immutable proposal.

The judge cannot choose runtime directives. Confidence is assessment confidence,
not probability of harm and is never multiplied by an operational-risk score.

## Uniform HITL and digest integrity

Every HITL exposes the universally valid decisions:

- `approve_continue`;
- `approve_continue_read_only`;
- `modify_and_continue`;
- `request_more_evidence`;
- `replan`;
- `ask_clarification`;
- `abort`.

The seven controls are always present. `modify_and_continue` still requires a
typed, valid edit at submission time; it cannot bypass the proposal contract
when no editable payload exists. A review request includes operation id, digest,
proposal summary, target/destination, judge findings, and the proposal where
applicable.

Approval is bound to the exact current digest. Modification creates a new
immutable proposal and digest, invalidates the earlier approval and judge
assessment, clears any executable tool request, and enters `READY_GOVERNANCE`.
It never routes directly to Tool Gateway or an external executor.
`approve_continue_read_only` rejects an unchanged mutating proposal.

LangGraph `interrupt()` and `Command(resume=...)` remain the only pause/resume
mechanism. Official LangGraph checkpoints are authoritative; trace, dashboard
artifacts, and behavioral SQLite are read models.

## Execution status and deferred backends

This refactor provides typed normalization and an
`ExternalOperationExecutorPort`. The production
`DeferredExternalOperationExecutor` verifies the approval digest and always
returns `executed=false`. It does not send email, connect to enterprise
databases, execute SQL, mutate external systems, add credentials, or perform any
side effect. Test scenarios use deterministic fake proposals only.

## Observability

`layer2_trace.v3` keeps its top-level shape. Additive actor/HITL fields expose
the current proposal and digest; governance pillar results expose `active`,
`shadow`, `not_applicable`, `unavailable`, and `decision_influence`. Legacy v1/v2
normalization and checkpoint compatibility fields remain supported.

Primary implementation locations:

- `src/layer2_runtime_governance/external_operations/`
- `src/layer2_runtime_governance/governance_components/`
- `src/layer2_runtime_governance/judge/`
- `src/layer2_runtime_governance/signal_services/`
- `src/layer2_runtime_governance/runtime/governance_service.py`
- `src/layer2_runtime_governance/hitl/`
- `configs/opa/layer2_governance/`
