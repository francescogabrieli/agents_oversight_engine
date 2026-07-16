# Policy Decision Point

This directory is the deployment boundary for Layer 2 policy decisions. OPA owns policy interpretation and the final deterministic decision; LangGraph remains the Policy Enforcement Point (PEP).

Start the local service from the repository root:

```powershell
docker compose -f services/policy_decision_point/compose.yaml up -d
```

The service listens on `http://127.0.0.1:8181`. Override the runtime endpoint with `LAYER2_GOVERNANCE_OPA_BASE_URL` when replicas are placed behind a load balancer.

Policy bundles are mounted read-only from `configs/opa/layer2_governance`. The service is stateless, so replicas can be added without sharing runtime state. Version fields in each decision contract prevent a runtime from silently consuming an incompatible bundle.

Useful checks:

```powershell
docker compose -f services/policy_decision_point/compose.yaml ps
docker compose -f services/policy_decision_point/compose.yaml logs policy-decision-point
```
