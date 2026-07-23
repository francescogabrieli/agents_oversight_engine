# External operations

This capability owns the small canonical contract used whenever a runtime action may
cross the local boundary. `contracts.py` defines immutable proposals and typed payloads;
`normalizers.py` derives boundary, effect, destination, preview, and digest from runtime
actions or registry facts; `execution.py` exposes the digest-bound execution port.

The package is framework-independent. SQL normalization uses `sqlglot` only to parse a
single statement and derive AST-owned statement facts. It never executes SQL. The
production executor is deliberately deferred and performs no email, database, publish,
or generic side effect.

To extend the capability, add a bounded payload dataclass and a capability-specific
normalizer, then map its normalized facts in OPA static policy. Do not add semantic harm
classification to a normalizer; that belongs to the judge.
