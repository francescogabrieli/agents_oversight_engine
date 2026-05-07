# Governance and HITL Contract

This document extracts the current runtime boundary between:

- `governance`
- `oversight`
- `human_review` / `interrupt-resume`
- the future `external_communication` branch

It is intentionally operational, not aspirational: the goal is to make the current control contract explicit so a future dashboard can expose it without inventing new behavior.

## Boundary Rules

1. `governance` is the hard control point after actor execution.
2. `oversight` can only make the runtime more conservative.
3. `human_review` is the only pause/resume path for typed operator intervention.
4. `external_communication` must remain gated behind evidence, draft, and human review.
5. The dashboard is a view and decision surface over this contract, not a second policy engine.

## Implementation Audit

The table below separates what the code already implements from what is only a documented future slot.

| Contract item | Status | Where it lives |
| --- | --- | --- |
| `governance` is the hard control point after actor execution | Implemented | `src/layer2_runtime_governance/governance.py`, `src/layer2_runtime_governance/nodes.py` |
| `oversight` can only make the runtime more conservative | Implemented | `src/layer2_runtime_governance/governance.py`, `src/layer2_runtime_governance/oversight.py` |
| `human_review` is the only pause/resume path for typed operator intervention | Implemented | `src/layer2_runtime_governance/hitl.py`, `src/layer2_runtime_governance/graph.py` |
| `external_communication` stays gated behind evidence, draft, and human review | Future slot | `docs/governance_hitl_contract.md` only for now |
| The dashboard is a view and decision surface over this contract | Implemented local HTML control plane | `src/main_layer2_dashboard.py`, `src/layer2_runtime_governance/hitl_dashboard.py` |

## Dashboard Data Model

The dashboard should be derived from `TaskState` plus the pending human review payload.

Already available in code:

- current runtime state
- current plan step
- latest governance directive
- latest oversight summary
- reason codes
- pending human review request
- allowed human review decisions
- resume state
- final or aborted state

Already typed in the runtime:

- `TaskState`
- `GovernanceDecision`
- `OversightAssessment`
- `HumanReviewRequest`
- `HumanReviewDecision`
- `HumanReviewDecisionType`

## Decision Matrix

| Situation | Decision owner | Runtime directive | Runtime state | Operator / dashboard implication |
| --- | --- | --- | --- | --- |
| Actor outcome is policy-compliant and evidence is sufficient | `governance` with no tightening from `oversight` | `continue` or `continue_read_only` | `ACTING` or next-step progression | Show current step and allow runtime to proceed without interruption |
| Actor proposes a tool-backed action that remains within the read-only envelope | `governance` | `continue_read_only` | `READY_TOOL_EXECUTION` or continued runtime progression | Show that execution is constrained but still live |
| Policy allows progress, but deterministic oversight says more evidence is needed | `oversight` combined by `governance` | `request_more_evidence` | `ACTING` | Show that the runtime should gather more evidence; no human decision required yet |
| Actor step is semantically misaligned with the plan and needs correction | `governance` or actor self-validation | `replan` | `REPLANNING` | Show that the current trajectory is no longer stable and the planner must replace the active step |
| Policy explicitly requires human review | `governance` | `wait_human_review` | `WAITING_HUMAN_REVIEW` | Show a pause card with the typed review payload and approved actions |
| Policy requires clarification because input is incomplete or ambiguous | `governance` | `ask_user_clarification` | `WAITING_HUMAN_REVIEW` | Show a clarification-oriented review card; operator must resolve the missing information |
| Policy denies the actor outcome | `governance` | `stop` | `ABORTED` | Show a terminal denial state; no resume path should be offered |
| Human reviewer approves continuation | `human_review` | resume to the stored state | Usually `ACTING`, `READY_NEXT_STEP`, or `COMPLETED` | Return control to the runtime using the typed resume payload |
| Human reviewer asks for more evidence | `human_review` | resume after restoring the step | `ACTING` | Keep the task live, but force another evidence-gathering cycle |
| Human reviewer requests replanning | `human_review` | resume to replanning | `REPLANNING` | Show that the current plan was invalidated and must be replaced |
| Human reviewer aborts | `human_review` | terminal stop | `ABORTED` | Show final termination; no further action should be possible |

## Current Human Review Actions

The current review contract is typed through `HumanReviewDecisionType`:

- `approve_continue`
- `approve_continue_read_only`
- `request_more_evidence`
- `replan`
- `ask_clarification`
- `abort`

These are the only actions a dashboard should expose as canonical operator choices.

## What the Dashboard Should Display

The UI should be derived from `TaskState` and the pending review payload, not from ad hoc UI logic.

Minimum display fields:

- current runtime state
- current plan step
- latest governance directive
- latest oversight summary
- reason codes
- pending human review request
- allowed human review decisions
- resume state
- final or aborted state, if terminal

## What the Dashboard Should Not Do

- invent new directives
- bypass `governance`
- bypass `oversight`
- mutate runtime state directly
- execute `external_communication` without a typed draft/review boundary

## Current Code Coverage

These parts of the contract are already directly reflected in code:

- `governance` directive selection and conservative combination logic
- deterministic oversight scoring and recommendation
- typed HITL interrupt/resume request and decision handling
- explicit runtime states for `WAITING_HUMAN_REVIEW`, `REPLANNING`, `ABORTED`, and normal progression
- persisted governance, oversight, and human review history in `TaskState`

These parts still need a UI or an execution branch:

- an `external_communication` workflow with evidence, draft, and execution phases

## Future `external_communication` Slot

The next safe extension point is not direct execution. It is a typed flow with at least these phases:

1. evidence collection
2. draft proposal
3. human review
4. externally impactful execution

That branch should reuse the same boundary logic as the rest of Layer 2:

- governance decides whether the branch is allowed
- oversight can only tighten the decision
- human review is required before impact outside the runtime boundary

## Code Pointers

- `src/layer2_runtime_governance/governance.py`
- `src/layer2_runtime_governance/oversight.py`
- `src/layer2_runtime_governance/hitl.py`
- `src/layer2_runtime_governance/hitl_dashboard.py`
- `src/main_layer2_dashboard.py`
- `src/layer2_runtime_governance/state.py`
- `src/layer2_runtime_governance/nodes.py`
- `src/layer2_runtime_governance/graph.py`

## Practical Reading

If a task is paused, the runtime is saying one of four things:

- the policy envelope is too strict for autonomous continuation
- the evidence is not yet sufficient
- the plan no longer fits the observed trajectory
- a human must decide before the task can continue

That is the real contract the dashboard must expose.
