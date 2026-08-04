"""Layer 3: Tool Bus and governed external capabilities.

This package owns capability registries, Tool Gateway adapters, and the
post-governance external-operation boundary. Layer 2 may call these ports, but
the capability implementations do not own runtime routing or policy decisions.
"""

