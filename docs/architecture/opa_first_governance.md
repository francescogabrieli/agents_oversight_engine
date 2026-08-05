# OPA-First Layer 2 Governance

This note records the runtime ownership split after the OPA-first migration.

## Ownership

Python/LangGraph is the policy enforcement point.
It reads `TaskState`, builds versioned JSON facts, invokes local signal services, calls OPA, validates the OPA output, and applies the returned directive.

OPA is the policy decision point.
It owns static policy, thresholds, weights, precedence, caps, invariants, and the final directive.

Signal services are fact producers only.
Python signal services emit typed raw facts and unscored components. Rego alone computes scores, applies thresholds, requests the judge, and selects the final runtime directive.

## Runtime Flow

1. `nodes.governance` calls `evaluate_opa_first_governance`.
2. `governance_input_builder.build_governance_base_facts` builds base facts from `TaskState`.
3. OPA `data.layer2.governance.required_pillars` decides optional pillar requirements.
4. Python invokes deterministic evidence, risk, and trajectory services, plus behavioral and judge when required.
5. `GovernanceDecisionInput` is validated and sent to OPA `data.layer2.governance.decision`.
6. Python validates `GovernanceDecisionOutput`.
7. Python records `GovernanceDecisionTrace` and applies the returned directive.

If OPA cannot return a valid decision, Python performs only a traced fail-closed transition to human review. It does not fall back to the Python decision combiner in the runtime path.
