# Planning capability

This package owns semantic planning and replanning ports, prompts, schemas, and
OpenAI adapters. The runtime consumes their typed decisions; it must not embed
planning-provider logic.

Use `planning` for first plans and `planning.replanning` for replacement
trajectories. The root `replanning.py` module is a compatibility facade only.
