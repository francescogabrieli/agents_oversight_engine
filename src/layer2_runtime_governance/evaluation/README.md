# Offline evaluation

This package evaluates exported Layer 2 traces without importing or starting the
live LangGraph runtime. Its public API is exposed from `evaluation.__init__` for
backward-compatible imports.

Extend report schemas and metrics here; do not add live runtime orchestration or
artifact persistence to this package.
