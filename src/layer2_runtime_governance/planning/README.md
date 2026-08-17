# Planning capability

This package owns semantic planning and replanning ports, prompts, schemas, and
their provider adapters. The production defaults use OpenAI structured responses
with `OPENAI_API_KEY`. The runtime consumes typed decisions and must not embed
planning-provider logic.

Configure model, API key, and timeout through the corresponding OpenAI
environment variables or `.env` values.

Use `planning` for first plans and `planning.replanning` for replacement
trajectories. The root `replanning.py` module is a compatibility facade only.
