# Proposed Training Plan for Master's Thesis

This document is a faithful working translation of the original training plan for
the Master's thesis. It preserves the source hierarchy between autonomous agents,
Human Oversight, human feedback, and the expected practical competencies.

---

# OBJECTIVES

In recent years, intelligent artificial agents have evolved from a niche research
topic into central components of digital products, services, and business processes.
Thanks to LLMs such as ChatGPT, Google Gemini, and Claude, and frameworks such as
LangChain, Semantic Kernel, AutoGen, MCP, Copilot, LangGraph, and CrewAI, it is now
possible to design agents capable of interacting, planning, orchestrating software
services, and adapting to context with a high degree of autonomy. In this scenario,
however, the main challenge is not only to make agents work, but to make them
controllable, reliable, and aligned with organizational goals and constraints.

The training path introduces the fundamental principles of autonomous AI agents with
an explicitly Human Oversight-oriented perspective. It presents the key concepts of
the agent lifecycle - perception, planning, action, and learning - and connects them
to uncertainty management: how to recognize uncertainty, represent it, and use it to
decide when an agent may act autonomously and when a human must be involved.

The path covers different human-agent interaction models - Human-in-the-Loop,
Human-on-the-Loop, and Human-in-Command - and how to translate them into concrete
design choices: confidence thresholds, escalation rules, use cases that always
require human validation, and interfaces that make supervisor intervention simple
and natural.

In parallel, it addresses continuous agent adaptation and improvement through human
feedback. Feedback from users and domain experts should be collected, structured,
and used to refine prompts, policies, tools, and agent configurations. Attention is
directed not only toward technical effectiveness, but also toward security,
traceability, transparency for end users, and organizational accountability.

---

# EXPECTED COMPETENCIES

From an operational perspective, the training path covers major AI-agent frameworks
and orchestration and workflow tools, using Python as the reference prototyping
language. Practical examples focus on conversational and task-oriented agents
integrated with external services, always designed with human control points, logs,
and targeted metrics: accuracy, human intervention rate, feedback quality, and
behavior under uncertainty.
