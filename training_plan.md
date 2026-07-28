# Proposed Training Plan for Master's Thesis

This document outlines the **training plan proposed to me for my Master’s thesis**.

---

# OBJECTIVES

In recent years, artificial intelligent agents have evolved from a niche research topic into core components of digital products, services, and business processes. Thanks to large language models such as ChatGPT, Google Gemini, and Claude, and frameworks like LangChain, Semantic Kernel, AutoGen, MCP, Copilot, LangGraph, and CrewAI, it is now possible to design agents capable of interacting, planning, orchestrating software services, and adapting to context with a high degree of autonomy.

For this thesis, autonomous agency is a primary objective rather than a secondary implementation detail. The proof of concept should evolve toward an agent that can take a semantic plan, execute it step by step, select and call tools through the runtime, reason over intermediate evidence, self-check progress, and continue until the plan is completed, blocked, or no longer valid. Human oversight becomes meaningful only when the system has real execution autonomy and can decide when to stop itself, ask for help, or escalate based on uncertainty, confidence, risk, and policy constraints.

In this scenario, however, the main challenge is no longer just to “make agents work,” but to ensure they operate in a controlled, reliable, and aligned manner with organizational goals and constraints.

---

## Core Topics

The training plan should be understood as covering three connected areas: **Autonomous Agent Execution**, **Human Oversight**, and **Feedback, Evaluation, and Continuous Improvement**. HITL, escalation, and confidence-based stopping are not substitutes for autonomy; they are oversight mechanisms that become meaningful only after the runtime can act, reason, and progress through a plan without being reduced to a single LLM call wrapped in orchestration code.

The training plan focuses on two main areas: **Human Oversight**—including Human-in-the-Loop, Human-on-the-Loop, and Human-in-Command paradigms—and **Feedback, Evaluation, and Continuous Improvement** of AI agents.

The course introduces the key concepts of the agent lifecycle—perception, planning, action, and learning—connecting them to the problem of uncertainty management: how to recognize it, represent it, and use it to determine when an agent can act autonomously and when human intervention is required.

It also explores different models of human–agent interaction (Human-in-the-Loop, Human-on-the-Loop, Human-in-Command) and how to translate them into concrete design decisions, such as defining confidence thresholds, designing escalation rules, identifying use cases that always require human validation, and building interfaces that make human supervision intuitive and effective.

In parallel, the topic of continuous agent improvement through human feedback is addressed. The course demonstrates how to collect, structure, and leverage feedback from users and domain experts to refine prompts, policies, tools, and agent configurations.

The focus is not only on technical effectiveness, but also on key aspects such as security, traceability, transparency toward end users, and organizational accountability.

