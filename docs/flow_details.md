# Runtime Flow Details

This document describes the current debug-level flow of the repository.
It is not the user-facing view. It shows what happens internally across Layer 1, Layer 2, governance, HITL, the Tool Gateway, trace export, and the local dashboard.

## Example Task

Input:

```json
{
  "raw_task_text": "Analizza questo testo e dimmi quali parole ricorrono di piu: 'agent agent governance MCP evidence evidence evidence'.",
  "context_hint": "Usa strumenti read-only se utili."
}
```

Expected user-facing result:

```text
Nel testo fornito, la parola piu frequente e "evidence" con 3 occorrenze.
Segue "agent" con 2 occorrenze.
Le parole "governance" e "MCP" compaiono una volta ciascuna.
```

The user does not see planner internals, governance scoring, Tool Gateway details, or MCP transport.
Those parts are internal and exist for control, evidence, auditability, and debugging.

## Current Runtime Shape

The current Layer 2 slice is:

```text
START
  -> bootstrap_from_intake
  -> planner
  -> actor
  -> governance
  -> { tool_gateway | replanner | human_review | finalize_bootstrap }
  -> observe_tool_result
  -> actor
  -> governance
  -> { tool_gateway | replanner | human_review | finalize_bootstrap }
  -> finalize_bootstrap
  -> END
```

Important points:

- planner output is semantic and typed
- actor output is typed and step-bounded
- tool use is read-only and routed through the Tool Gateway
- governance is the hard control point after actor execution
- oversight can only make the decision more conservative
- HITL uses typed `interrupt / resume`
- the dashboard is a read-only control plane over the same contract

## 1. Layer 1 Intake

Layer 1 converts the raw task into a canonical `IntakeResult`.
The runtime no longer works directly on the free-form user text after this step.

Conceptual intake payload:

```json
{
  "structured_task": {
    "task_spec": {
      "goal": "Analyze the supplied text and identify the most frequent words.",
      "context": "Use read-only tools if useful.",
      "constraints": [
        "Do not modify files or external systems.",
        "Use read-only tools only."
      ],
      "domain_type": "analysis",
      "domain_label": "text_analysis",
      "sensitivity": "low",
      "success_criteria": [
        "Return the most frequent words.",
        "Base the result on the supplied text."
      ]
    },
    "execution_policy": {
      "policy_profile_name": "default",
      "confidence_threshold": 0.65,
      "budget": {
        "max_steps": 5,
        "max_tool_calls": 10
      },
      "read_only_mode": false
    }
  },
  "metadata": {
    "parse_status": "complete",
    "parser_name": "llm_task_parser",
    "prompt_version": "v1",
    "schema_version": "1.0.0",
    "warnings": [],
    "missing_information": []
  }
}
```

## 2. Bootstrap Into Layer 2

`bootstrap_from_intake` converts `IntakeResult` into `TaskState`.
This initializes runtime state, plan cursor, counters, governance history, oversight history, and HITL bookkeeping.

Conceptual bootstrap state:

```json
{
  "current_state": "planning",
  "plan_steps": [],
  "current_plan_step_index": null,
  "current_tool_request": null,
  "last_tool_result": null,
  "observed_evidence": [],
  "scratchpad": [],
  "step_validations": [],
  "governance_history": [],
  "oversight_history": [],
  "human_review_history": [],
  "tool_call_count": 0,
  "actor_turn_count": 0
}
```

## 3. Planner

The planner produces a semantic plan.
It does not emit concrete tool calls or execution payloads.

Example plan:

```json
{
  "plan_steps": [
    {
      "step_id": "step_1",
      "objective": "Compute text statistics for the supplied text.",
      "rationale": "The task requires identifying repeated words before synthesis.",
      "expected_outcome": "A compact frequency summary for the supplied text.",
      "status": "in_progress"
    },
    {
      "step_id": "step_2",
      "objective": "Synthesize the final answer from the computed statistics.",
      "rationale": "The user expects a readable explanation, not only raw counts.",
      "expected_outcome": "A concise final response listing the most frequent words.",
      "status": "pending"
    }
  ],
  "current_plan_step_index": 0
}
```

## 4. Actor

The actor receives the full plan as context, but it can act only on the current step.
It may:

- propose a read-only tool request
- produce local output
- request clarification
- signal replan
- complete the step when evidence is sufficient

Example actor output for the first step:

```json
{
  "plan_step_id": "step_1",
  "result_type": "propose_action",
  "summary": "Request read-only MCP text statistics for the supplied text.",
  "capability_mode": "read_only",
  "external_impact": false,
  "tool_request": {
    "plan_step_id": "step_1",
    "tool_name": "mcp.local_test.text_stats",
    "reason": "The current step requires deterministic word frequency statistics.",
    "text": "agent agent governance MCP evidence evidence evidence",
    "limit": 5
  },
  "scratchpad_entry": {
    "turn_index": 1,
    "plan_step_id": "step_1",
    "entry_type": "tool_decision",
    "summary": "Text statistics are needed to satisfy the current step."
  }
}
```

At this point the runtime transitions to a tool-execution state and sends the request to governance.

## 5. Governance and Oversight

Governance evaluates the actor result and the current runtime context.
It combines:

- OPA policy
- deterministic oversight
- runtime evidence and trajectory context

Oversight can only preserve or tighten the policy decision.
It cannot make the runtime more permissive.

Simplified governance input:

```json
{
  "task_domain_type": "analysis",
  "task_domain_label": "text_analysis",
  "task_sensitivity": "low",
  "read_only_mode": false,
  "actor_result_type": "propose_action",
  "capability_mode": "read_only",
  "external_impact": false,
  "tool_name": "mcp.local_test.text_stats",
  "tool_has_query": false,
  "tool_has_url": false,
  "tool_call_count": 0,
  "observed_evidence_count": 0,
  "budget_max_tool_calls": 10
}
```

Typical outcome:

```json
{
  "policy_decision": "allow_read_only",
  "directive": "continue_read_only",
  "policy_directive": "continue_read_only",
  "oversight_recommended_directive": "continue",
  "summary": "Read-only tool use is allowed. Oversight recommended continue and preserved the policy directive continue_read_only.",
  "evaluated_tool_name": "mcp.local_test.text_stats"
}
```

If the policy requires human review, the runtime enters `WAITING_HUMAN_REVIEW`.
If the policy denies the action, the runtime enters `ABORTED`.

## 6. Tool Gateway

When governance allows the read-only action, the request goes through the Tool Gateway.
The actor never calls MCP directly.

The gateway:

- checks registry configuration
- validates the tool capability
- maps typed request fields to backend arguments
- normalizes backend results into `ToolExecutionResult`

Example normalized MCP call:

```json
{
  "tool_name": "mcp.local_test.text_stats",
  "reason": "The current step requires deterministic word frequency statistics.",
  "text": "agent agent governance MCP evidence evidence evidence",
  "limit": 5
}
```

The backend response is normalized into structured tool output and stored in runtime state.

## 7. Observe Tool Result

`observe_tool_result` converts the tool result into explicit evidence.
It also appends audit-safe scratchpad entries.

Example evidence:

```json
{
  "evidence_id": "step_1:tool:1:source:1",
  "plan_step_id": "step_1",
  "title": "mcp.local_test.text_stats",
  "locator": "mcp://local_test/text_stats",
  "excerpt": "{\"characters\": 53, \"lines\": 1, \"top_words\": [[\"evidence\", 3], [\"agent\", 2], [\"governance\", 1], [\"mcp\", 1]], \"words\": 7}",
  "source_tool": "mcp.local_test.text_stats"
}
```

The graph then returns to the actor with the new evidence attached.

## 8. Actor Completes the Step

The actor sees the evidence and produces a validated step result.

Example validated output:

```json
{
  "plan_step_id": "step_1",
  "result_type": "intermediate_output",
  "summary": "Text statistics were computed successfully.",
  "capability_mode": "none",
  "external_impact": false,
  "output_text": "The most frequent word is evidence, followed by agent.",
  "step_validation": {
    "plan_step_id": "step_1",
    "objective_satisfied": true,
    "evidence_sufficient": true,
    "confidence": 0.9,
    "next_action": "complete_step",
    "rationale_summary": "The MCP text statistics satisfy the current step objective."
  }
}
```

The runtime marks the step completed and advances the plan cursor.

## 9. Final Step

The actor performs the synthesis step and emits the final output.

Example final output:

```json
{
  "plan_step_id": "step_2",
  "result_type": "final_output",
  "summary": "Final answer produced from MCP text statistics.",
  "capability_mode": "none",
  "external_impact": false,
  "output_text": "Nel testo fornito, la parola piu frequente e 'evidence' con 3 occorrenze. Segue 'agent' con 2 occorrenze. Le parole 'governance' e 'MCP' compaiono una volta ciascuna.",
  "step_validation": {
    "plan_step_id": "step_2",
    "objective_satisfied": true,
    "evidence_sufficient": true,
    "confidence": 0.9,
    "next_action": "complete_step",
    "rationale_summary": "The final answer is grounded in the observed MCP text statistics."
  }
}
```

After governance accepts the final local output, the runtime reaches `finalize_bootstrap` and terminates.

## 10. HITL Branch

If governance routes to `WAITING_HUMAN_REVIEW`, the runtime pauses and exposes a typed human review request.

The active contract is:

- `governance` decides whether a pause is required
- `oversight` can only tighten the decision
- `human_review` resumes through typed decisions

Canonical human review decisions:

- `approve_continue`
- `approve_continue_read_only`
- `request_more_evidence`
- `replan`
- `ask_clarification`
- `abort`

The local dashboard renders this snapshot as a read-only HTML control plane.
It also exposes live refresh through client-side polling of `/snapshot.json`.
When the CLI sees the first `WAITING_HUMAN_REVIEW` transition for a thread, it auto-launches the same dashboard session in a background server thread so the operator does not need to open it manually.

## 11. Trace Export

At the end of a run, `src/layer2_runtime_governance/trace.py` builds a typed, JSON-safe trace from `TaskState`.
The trace is read-only. It is not a second runtime state.

It is used for:

- debug output
- thesis evidence
- dashboard payloads
- offline evaluation

The CLI can print the trace, but the trace itself remains derived from the runtime state.

## Current Thesis Reading

The current architecture demonstrates this split:

```text
User task
  -> Layer 1 Intake
  -> TaskState
  -> Planner
  -> Actor
  -> Governance
     -> OPA policy
     -> deterministic oversight
     -> decision combiner
  -> Tool Gateway
  -> MCP backend
  -> ToolExecutionResult
  -> ObservedEvidence
  -> Actor
  -> HITL if needed
  -> Final output
  -> Trace export
  -> Dashboard snapshot
```

The important constraint is that the dashboard and the trace are views over the same runtime contract.
They must not become parallel sources of truth.
