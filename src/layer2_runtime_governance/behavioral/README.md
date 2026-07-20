# Behavioural capability

This package owns the non-authoritative SQLite behavioural read model, feature
extraction, similarity scoring, and persistence projections. It cannot issue a
final governance directive; OPA remains the final authority.

`service.py` owns the implementation and `__init__.py` exposes the bounded public
API. Extend scoring or storage adapters here while keeping runtime state
canonical in the domain and graph layers.
