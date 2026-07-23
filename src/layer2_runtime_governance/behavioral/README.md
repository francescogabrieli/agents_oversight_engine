# Behavioural capability

This package owns the shadow-only SQLite behavioural read model, feature
extraction, similarity/deviation/novelty scoring, counterfactual recommendation,
and persistence projections. Assessments export `mode="shadow"` and
`decision_influence=false`; OPA does not branch on them and they are never passed
to the judge.

`service.py` owns the implementation and `__init__.py` exposes the bounded public
API. Extend scoring or storage adapters here while keeping runtime state
canonical in the domain and graph layers. One assessment is reused within a run;
SQLite materialization occurs at stable review/completion/abort boundaries.
