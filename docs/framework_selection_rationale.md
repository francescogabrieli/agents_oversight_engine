# Framework Selection Rationale

_Date: 2026-04-23_

## 1. Purpose of this document

This note explains which agent framework should be used as the **base runtime** for the thesis and why.

The goal of the thesis is **not** to reinvent an agent framework from scratch and **not** to prove that an agent can call tools in a loop. The real contribution is higher-level:

- design an **autonomous agent** that can operate with a convincing degree of autonomy;
- add a **Human Oversight** architecture that is richer than basic built-in guardrails;
- support **HITL / HOTL / HIC** patterns as explicit design choices;
- introduce an **adaptive self-assessment layer** that can stop, replan, ask for evidence, or escalate even when no hard policy has fired;
- support **continuous improvement** from traces, human feedback, and historical runs.

In other words, the framework must be good at **spawning and running a real agent**, but the thesis value must live in the architecture built **on top of** that framework.

---

## 2. Thesis-driven selection criteria

The framework was evaluated against the following criteria.

### A. Must give us a real autonomous agent quickly
The framework must already support the basic agent loop:
- LLM calls
- tool calling
- iterative execution
- memory/state or resumability
- streaming or progress events

### B. Must leave room for the thesis contribution
The framework should not force the thesis to collapse into “we used the framework’s built-in HITL/guardrails”.

We need room to implement our own architecture on top:
- external **OPA** policy envelope
- adaptive **confidence / risk / trajectory** engine
- **LLM-as-a-judge** semantic oversight
- **decision combiner**
- **continuous improvement** layer from feedback and traces

### C. Must support autonomy without exposing mechanical internals to users
Internally the runtime can be step-based, but externally the agent should still appear autonomous and coherent.

### D. Must be suitable for a thesis, not only for a demo
The framework should support:
- observability
- persistence/checkpointing or safe resumability
- controlled execution
- integration with external components
- experimentation and future extension

### E. Must not force the whole solution into one opinionated pattern
We do not want a framework that already defines the entire oversight architecture in almost the same shape as the thesis.

---

## 3. Frameworks analyzed

The comparison below focuses on the main frameworks currently prominent in the agent ecosystem.

### 3.1 LangGraph

**What it is**
A low-level orchestration/runtime layer oriented toward agentic systems, durable execution, interrupts, streaming, and human-in-the-loop.

**Why it is strong for this thesis**
- It gives a real agent runtime without hiding too much control logic.
- It fits well with a **plan-and-execute** core and a local **ReAct-like actor**.
- It is flexible enough to treat the framework as an **internal orchestration substrate**, while keeping policy, adaptive oversight, and improvement logic outside or above the graph.
- It supports persistence, interrupts, and resumability, which are useful for selective human review.
- The Functional API is especially attractive when we want autonomy and normal code structure without forcing every idea into a visibly rigid graph.

**Why it is not a perfect choice**
- It already has native concepts around interrupts, HITL, persistence, and orchestration.
- If misused, the thesis can look like “LangGraph features + minor customization”.
- The implementation must therefore be disciplined: LangGraph should be the runtime substrate, not the scientific contribution.

**Verdict**
Best overall fit **if** we are explicit that the contribution is the **oversight architecture**, not LangGraph’s native HITL features.

---

### 3.2 LangChain Agents

**What it is**
A higher-level agent abstraction with a prebuilt agent loop and middleware system.

**Why it is attractive**
- Fastest path to a working agent.
- Middleware gives easy access to guardrails, tool gating, retries, and HITL.
- Good for baselines and quick prototypes.

**Why it is weaker for this thesis**
- It is intentionally more prebuilt and more opinionated.
- The thesis risks drifting toward “agent with middleware” instead of “autonomous agent with a custom oversight architecture”.
- Since LangChain agents are already built around a standard loop and middleware hooks, the room for architectural authorship is smaller than with LangGraph.

**Verdict**
Very good for a fast product prototype or baseline, but not my first choice as the main thesis framework.

---

### 3.3 OpenAI Agents SDK

**What it is**
A high-level SDK centered on agents, tools, handoffs, sessions, results, streaming, and guardrails.

**Why it is attractive**
- Excellent developer experience.
- Very fast path to a strong autonomous-feeling agent.
- Good support for tools, streaming, orchestration patterns, and production-oriented developer ergonomics.
- Good candidate if the priority is “get a polished agent running fast”.

**Why it is weaker for this thesis**
- It is more opinionated about what an agent is and how orchestration is handled.
- Guardrails, handoffs, sessions, and other built-ins are close to the thesis problem-space.
- The risk of the work being read as “using the SDK well” is higher.
- It is better as an **agent product SDK** than as the base substrate for an architecture-heavy thesis.

**Verdict**
Strong second option, especially for product speed. Not my preferred primary framework for this thesis.

---

### 3.4 Microsoft Agent Framework

**What it is**
Microsoft’s newer framework for agents and workflows, positioned as the successor to ideas from AutoGen and Semantic Kernel, with workflows, state management, middleware, and HITL support.

**Why it is attractive**
- Very relevant for enterprise agent scenarios.
- Strong workflow orientation.
- Good support for long-running, stateful, human-in-the-loop workflows.
- A serious alternative if the ecosystem is already Azure / Microsoft-centric.

**Why it is weaker for this thesis**
- It is still more framework-shaped than I would want for this particular thesis.
- If there is no Microsoft/Azure ecosystem constraint, the extra abstraction is not clearly worth the tradeoff.
- The thesis contribution could still be overshadowed by the framework’s workflow abstractions.

**Verdict**
A credible alternative, but not the best fit unless the industrial context is already Microsoft-first.

---

### 3.5 Semantic Kernel

**What it is**
A Microsoft SDK for building AI applications and agents, with plugins, filters, agent patterns, and process-oriented workflows.

**Why it is attractive**
- Enterprise-friendly.
- Good plugin model.
- Strong integration story in Microsoft-heavy stacks.

**Why it is weaker for this thesis**
- It is no longer the most compelling greenfield choice when Microsoft itself is clearly pushing **Agent Framework** as the next-generation direction.
- The thesis would likely have to justify why Semantic Kernel was chosen over its newer successor.

**Verdict**
Useful historically and still capable, but not the best primary choice for a new thesis project in 2026.

---

### 3.6 AutoGen / AG2

**What it is**
A historically influential framework for multi-agent conversations and coordination.

**Why it is attractive**
- Important in the history of agent frameworks.
- Good for multi-agent experimentation and conversational orchestration.
- Human feedback via user proxy patterns is well-known.

**Why it is weaker for this thesis**
- The Microsoft ecosystem now presents **Agent Framework** as the direct successor.
- As a greenfield thesis choice, it feels more like using a historically important framework than choosing the currently strongest long-term substrate.
- Better for background/comparison than for the final primary selection.

**Verdict**
Valuable to analyze, but not recommended as the final primary framework for this thesis.

---

### 3.7 CrewAI

**What it is**
A framework centered around crews, flows, collaboration patterns, guardrails, observability, and increasingly production-oriented multi-agent workflows.

**Why it is attractive**
- Productive and batteries-included.
- Good multi-agent collaboration story.
- Built-in HITL support is already practical.
- Good if the project goal is operational multi-agent automation fast.

**Why it is weaker for this thesis**
- It is more opinionated in its own conceptual vocabulary (`crews`, `flows`, `human_feedback`, etc.).
- The thesis could end up looking too close to CrewAI’s native mental model.
- It gives less of the “framework as neutral substrate” feeling than LangGraph.

**Verdict**
Good production-oriented framework, but less suitable when the thesis contribution is a custom oversight architecture.

---

### 3.8 Temporal

**What it is**
A durable execution platform and workflow engine. It is not primarily an agent framework, but it is increasingly used for long-running and reliable AI/agentic systems.

**Why it is attractive**
- Excellent reliability story.
- Very strong for long-running tasks, human intervention, retries, resumability, and failure handling.
- Very good choice if the thesis were mainly about durable execution and operational robustness.

**Why it is weaker for this thesis as the primary framework**
- It does not give the same “spawn a real agent quickly” experience as a dedicated agent framework.
- You would still need to build more of the agent layer yourself.
- It is best seen as **complementary infrastructure**, not the most natural first framework for this thesis.

**Verdict**
Excellent optional second-phase hardening layer; not my preferred primary framework for the thesis prototype.

---

### 3.9 LlamaIndex Agent Workflows

**What it is**
An agent and workflow system built around LlamaIndex workflows, events, and tools, with strong alignment to retrieval and knowledge workflows.

**Why it is attractive**
- Good event-driven workflow story.
- Solid if the thesis were retrieval-first or document/knowledge-agent-centric.
- Supports agent workflows, tools, state, streaming, and human-in-the-loop patterns.

**Why it is weaker for this thesis**
- The thesis focus is not primarily retrieval orchestration.
- It is a plausible choice, but not as central a fit as LangGraph for a runtime + oversight problem.

**Verdict**
Strong alternative for retrieval-heavy projects; not my top choice here.

---

### 3.10 PydanticAI

**What it is**
A code-first framework emphasizing typed agents, model/tool integrations, evaluation tooling, and now a graph API in beta.

**Why it is attractive**
- Strong developer ergonomics.
- Typed, explicit, and code-first.
- Interesting evaluation tooling.
- Promising for teams that want structured Python-native control.

**Why it is weaker for this thesis**
- The graph/orchestration layer is newer and less obviously mature than LangGraph for this specific use case.
- It is promising, but for a thesis with strong oversight and runtime concerns, I would currently trust LangGraph more as the primary substrate.

**Verdict**
Promising emerging option, but not my first choice for this thesis in its current state.

---

## 4. Comparative summary

| Framework | Fast path to a real agent | Leaves room for custom oversight | Native HITL / control support | Best role in this thesis | Final fit |
|---|---:|---:|---:|---|---:|
| LangGraph | High | High | High | **Primary runtime substrate** | **9/10** |
| LangChain Agents | Very High | Medium | High | Baseline / fast prototype | 7/10 |
| OpenAI Agents SDK | Very High | Medium | High | Product-speed alternative | 8/10 |
| Microsoft Agent Framework | High | Medium-High | High | Enterprise alternative | 7.5/10 |
| Semantic Kernel | Medium | Medium | Medium-High | Legacy/enterprise comparison | 6.5/10 |
| AutoGen | Medium | Medium | Medium | Historical comparison | 6/10 |
| CrewAI | High | Medium | High | Multi-agent product prototype | 6.5/10 |
| Temporal | Medium | Very High | High | Infrastructure hardening layer | 7.5/10 |
| LlamaIndex Workflows | High | Medium-High | Medium-High | Retrieval-centric alternative | 7/10 |
| PydanticAI | Medium-High | High | Medium | Emerging code-first option | 7/10 |

---

## 5. Final recommendation

## Recommended primary choice: **LangGraph**

### Why
LangGraph gives the best balance between:
- not reinventing the agent loop from scratch;
- still having a real autonomous agent runtime;
- still preserving enough architectural freedom for the thesis contribution to remain visibly **yours**.

It is low-level enough to let us define:
- a **plan-and-execute** agent core;
- a local **ReAct-like actor** for tool use;
- an external **OPA** policy layer;
- an adaptive **confidence / risk / trajectory** engine;
- a live **LLM-as-a-judge** component;
- a **decision combiner**;
- a post-run **continuous improvement** layer.

### How it should be used
The key design rule is:

> **LangGraph is the orchestration substrate, not the thesis contribution.**

That means:
- use LangGraph for agent execution, state, interrupts, and safe resume;
- do **not** present LangGraph’s built-in HITL or guardrail mechanisms as the core scientific novelty;
- keep the actual oversight architecture framework-independent in spirit, even if it runs on top of LangGraph.

---

## 6. Recommended stack

### Primary stack
- **LangGraph**: agent runtime / orchestration substrate
- **OpenAI models**: LLM provider for planner, actor, judge, and other model-backed components
- **OPA (Open Policy Agent)**: deterministic hard policy envelope
- **Custom adaptive oversight layer**: confidence / risk / trajectory + judge + decision combiner
- **Custom improvement layer**: feedback-driven refinement of prompts, thresholds, policies, and configuration

### Optional later hardening
- **Temporal** can be added later if the prototype evolves into a more production-grade, long-running, durability-heavy system.

---

## 7. Why this choice still fits the thesis contribution

Choosing LangGraph does **not** reduce the thesis to “using LangGraph features”, provided the architecture is framed correctly.

The thesis contribution remains in:
- the **three-pillar oversight model**;
- the distinction between **hard policy envelope** and **adaptive self-assessment**;
- the use of **semantic judging** beyond fixed policy triggers;
- the selective handling of **HITL / HOTL / HIC**;
- the **continuous improvement** loop from human feedback and historical traces.

In short:
- **LangGraph gives us the agent**;
- **the thesis gives us the oversight architecture**.

---

## 8. Alternative decision if priorities change

If priorities shift, the fallback choices would be:

### If the priority becomes “ship the fastest possible autonomous agent prototype”
Choose **OpenAI Agents SDK**.

### If the priority becomes “extreme enterprise alignment with Microsoft/Azure”
Choose **Microsoft Agent Framework**.

### If the priority becomes “durability and reliability above all else”
Choose **Temporal** plus a thinner custom agent layer.

### If the priority becomes “retrieval-first agent workflows”
Choose **LlamaIndex Agent Workflows**.

---

## 9. Final one-paragraph thesis-ready conclusion

For this thesis, the most balanced choice is **LangGraph** as the underlying agent runtime. It provides a real autonomous agent substrate with sufficient support for iterative tool-using execution, persistence, interrupts, and controlled orchestration, while still leaving enough architectural freedom for the thesis contribution to remain centered on a custom Human Oversight and Continuous Improvement architecture. More opinionated agent SDKs would accelerate productization, but they would also increase the risk that the thesis is perceived as a thin customization of framework-native oversight patterns rather than as an original architectural design focused on autonomous self-assessment, selective human escalation, and learning from feedback.

---

## 10. Official documentation consulted

- LangGraph overview: https://docs.langchain.com/oss/python/langgraph/overview
- LangGraph Functional API: https://docs.langchain.com/oss/python/langgraph/functional-api
- LangGraph interrupts: https://docs.langchain.com/oss/python/langgraph/interrupts
- LangChain agents: https://docs.langchain.com/oss/python/langchain/agents
- LangChain middleware overview: https://docs.langchain.com/oss/python/langchain/middleware/overview
- LangChain HITL middleware: https://docs.langchain.com/oss/python/langchain/human-in-the-loop
- OpenAI Agents SDK overview: https://openai.github.io/openai-agents-python/agents/
- OpenAI Agents SDK running agents: https://openai.github.io/openai-agents-python/running_agents/
- OpenAI Agents SDK guardrails: https://openai.github.io/openai-agents-python/guardrails/
- OpenAI platform agents guide: https://platform.openai.com/docs/guides/agents
- Microsoft Agent Framework overview: https://learn.microsoft.com/en-us/agent-framework/overview/
- Microsoft Agent Framework HITL workflows: https://learn.microsoft.com/en-us/agent-framework/workflows/human-in-the-loop
- Semantic Kernel overview: https://learn.microsoft.com/en-us/semantic-kernel/overview/
- Semantic Kernel Agent Framework: https://learn.microsoft.com/en-us/semantic-kernel/frameworks/agent/
- AutoGen AgentChat docs: https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/index.html
- AutoGen human-in-the-loop: https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/human-in-the-loop.html
- CrewAI docs: https://docs.crewai.com/
- CrewAI HITL: https://docs.crewai.com/en/learn/human-in-the-loop
- CrewAI human feedback in flows: https://docs.crewai.com/en/learn/human-feedback-in-flows
- Temporal AI cookbook: https://docs.temporal.io/ai-cookbook
- Temporal HITL AI agent: https://docs.temporal.io/ai-cookbook/human-in-the-loop-python
- Temporal workflows: https://docs.temporal.io/workflows
- LlamaIndex agent workflows (Python): https://developers.llamaindex.ai/python/examples/agent/agent_workflow_basic/
- LlamaIndex workflows introduction: https://developers.llamaindex.ai/python/llamaagents/workflows/
- LlamaIndex human in the loop: https://developers.llamaindex.ai/python/framework/understanding/agent/human_in_the_loop/
- PydanticAI changelog (graph API beta, tool approval related notes): https://ai.pydantic.dev/changelog.html
- Pydantic Graph beta docs: https://ai.pydantic.dev/api/pydantic_graph/beta_node/
- Pydantic eval lifecycle hooks: https://ai.pydantic.dev/evals/how-to/lifecycle/
