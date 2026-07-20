# Semantic judge capability

This package owns the optional typed semantic judge and its provider settings.
It returns advisory assessments only; OPA consumes those assessments to derive
the final directive.

`service.py` owns provider execution and prompt parsing; `__init__.py` is the
small public API. Provider-specific retries and parsing belong here, never in
runtime nodes. OPA alone decides whether the judge is required.
