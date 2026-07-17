# Semantic judge capability

This package owns the optional typed semantic judge and its provider settings.
It returns advisory assessments only; OPA consumes those assessments to derive
the final directive.

Provider-specific retries and parsing belong here, never in runtime nodes.
