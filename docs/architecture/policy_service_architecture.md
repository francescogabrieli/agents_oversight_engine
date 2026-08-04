# Policy service architecture

## Decision

Layer 2 uses one independently deployable Policy Decision Point (PDP), implemented by OPA, rather than embedding policy interpretation in the LangGraph process. The runtime is the Policy Enforcement Point (PEP): it collects typed facts, calls the PDP, validates the response, applies the directive, and records the trace.

```text
Planner / Actor / Tool loop
            |
            v
  LangGraph governance PEP
    |       |       |
    |       |       +-- optional judge facts
    |       +---------- behavioural facts
    +------------------ deterministic evidence/risk/trajectory facts
            |
            | versioned JSON over HTTP
            v
   OPA Policy Decision Point
            |
            v
 typed directive + reason codes + policy versions
```

## Ownership boundaries

| Boundary | Owns | Must not own |
|---|---|---|
| Layer 2 runtime | orchestration, state, signal collection, enforcement, HITL, trace persistence | policy precedence or threshold interpretation |
| Policy service client | HTTP transport and OPA `result` envelope | runtime dataclasses or business decisions |
| OPA PDP | hard policy, pillar interpretation, precedence, safety invariants, final directive | LangGraph state mutation or tool execution |
| Signal providers | raw facts and diagnostics for one pillar | the final governance directive |

## Structure

- `src/governance_policy_service`: runtime-independent HTTP client for the PDP.
- `src/layer2_runtime_governance/signal_services`: small providers that translate runtime observations into policy facts.
- `configs/opa/layer2_governance`: versioned policy bundle and pillar rules.
- `services/policy_decision_point`: local deployable OPA service definition.
- `schemas/governance_decision_*.schema.json`: versioned integration contracts.

## Scaling model

OPA is stateless for this use case. Production can run multiple identical replicas behind a service address and point `LAYER2_GOVERNANCE_OPA_BASE_URL` at that address. Policy bundles should be promoted as immutable versions. Runtime checkpoints, behavioural history, and HITL state remain outside the PDP.

Do not split each policy pillar into a network service by default. A pillar becomes a separate service only when it has an independent data lifecycle, scaling profile, security boundary, or failure policy. Network fan-out for deterministic in-process calculations would add latency and partial-failure modes without a useful ownership boundary.

## Failure behavior

- Missing or malformed required pillar: fail closed to human review.
- Unreachable PDP or invalid response: fail closed and retain the transport error in the governance trace.
- Bundle/config version mismatch: human review; never silently downgrade.
- Optional judge unavailable: follow the required-pillar decision returned by OPA.

This preserves the existing audit-safe behavior while making the policy engine independently deployable and replaceable.
