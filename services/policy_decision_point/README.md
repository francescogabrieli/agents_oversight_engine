# Policy Decision Point

This directory is the deployment boundary for Layer 2 policy decisions. OPA owns policy interpretation and the final deterministic decision; LangGraph remains the Policy Enforcement Point (PEP).

Start the local service from the repository root:

```powershell
docker compose -f services/policy_decision_point/compose.yaml up -d
```

The service listens on `http://127.0.0.1:8181`. Override the runtime endpoint with `LAYER2_GOVERNANCE_OPA_BASE_URL` when replicas are placed behind a load balancer.

Policy bundles are mounted read-only from `configs/opa/layer2_governance`. The service is stateless, so replicas can be added without sharing runtime state. Version fields in each decision contract prevent a runtime from silently consuming an incompatible bundle.

The local service starts OPA with `--watch`, so later edits under the mounted policy directory are reloaded automatically. An already-running container created before this option was added must be recreated once:

```powershell
docker compose -f services/policy_decision_point/compose.yaml up -d --force-recreate policy-decision-point
```

Verify the version loaded by the running server before starting an official run:

```powershell
(Invoke-RestMethod http://127.0.0.1:8181/v1/data/policy_config/policy_bundle_version).result
```

The reported value must match `OPAHttpPolicyEngine.policy_bundle_version`. A mismatch is a deployment/configuration error; do not resume an existing benchmark checkpoint after changing the policy bundle.

Useful checks:

```powershell
docker compose -f services/policy_decision_point/compose.yaml ps
docker compose -f services/policy_decision_point/compose.yaml logs policy-decision-point
```
