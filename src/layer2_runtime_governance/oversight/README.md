# Dynamic oversight capability

This package owns typed assessment and legacy-checkpoint contracts. Deterministic
evidence, operational-risk, and trajectory raw facts are built by
`signal_services`; OPA/Rego alone scores them and chooses the final directive.

New contracts belong in `contracts.py`. New raw facts belong in one focused
signal service; weights and thresholds remain only in the policy bundle.
