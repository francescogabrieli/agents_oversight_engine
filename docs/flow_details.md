# Runtime Flow Details

Questo documento descrive un esempio debug-level del flusso attuale del runtime.
Non rappresenta ciò che vede l'utente finale: mostra invece cosa succede internamente tra Layer 1, Layer 2, governance, Tool Gateway e backend MCP.

## Esempio Di Task

Input utente:

```json
{
  "raw_task_text": "Analizza questo testo e dimmi quali parole ricorrono di più: 'agent agent governance MCP evidence evidence evidence'.",
  "context_hint": "Usa strumenti read-only se utili."
}
```

Output atteso lato utente:

```text
Nel testo fornito, la parola più frequente è "evidence" con 3 occorrenze.
Segue "agent" con 2 occorrenze.
Le parole "governance" e "MCP" compaiono una volta ciascuna.
```

L'utente non vede planner, governance, Tool Gateway o chiamate MCP.
Questi passaggi sono interni e servono per controllo, audit, evidenza e debugging.

## Flusso Interno

Sequenza del grafo:

```text
START
  -> bootstrap_from_intake
  -> planner
  -> actor
  -> governance
  -> tool_gateway
  -> observe_tool_result
  -> actor
  -> governance
  -> actor
  -> governance
  -> finalize_bootstrap
  -> END
```

## 1. Layer 1 Intake

Il testo libero entra nel Layer 1 e viene trasformato in un `IntakeResult` canonico.

Esempio concettuale:

```json
{
  "structured_task": {
    "task_spec": {
      "goal": "Analizza il testo fornito e identifica le parole più ricorrenti.",
      "context": "Usa strumenti read-only se utili.",
      "constraints": [
        "Non modificare file o sistemi esterni.",
        "Usa solo strumenti read-only."
      ],
      "domain_type": "analysis",
      "domain_label": "text_analysis",
      "sensitivity": "low",
      "success_criteria": [
        "Restituire le parole più frequenti.",
        "Basare il risultato sul testo fornito."
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

Da questo punto in avanti il runtime non lavora più sul testo libero originale, ma su un contratto strutturato.

## 2. Bootstrap Layer 2

Il nodo `bootstrap_from_intake` converte `IntakeResult` in `TaskState`.

Stato iniziale semplificato:

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
  "tool_call_count": 0,
  "actor_turn_count": 0,
  "step_history": [
    {
      "step_index": 0,
      "runtime_state": "planning",
      "node_name": "bootstrap_from_intake",
      "summary": "Runtime state initialized from layer 1 intake payload."
    }
  ]
}
```

## 3. Planner

Il planner produce un piano semantico.
Non produce tool calls, payload eseguibili o istruzioni MCP.

Esempio:

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
  "current_plan_step_index": 0,
  "planner_name": "openai_layer2_planner",
  "planner_prompt_version": "planner_v1"
}
```

## 4. Actor: Prima Azione

L'actor riceve il piano completo come contesto, ma può agire solo sul current step.

Input rilevante:

```json
{
  "current_plan_step": {
    "step_id": "step_1",
    "objective": "Compute text statistics for the supplied text.",
    "status": "in_progress"
  },
  "observed_evidence": [],
  "last_tool_result": null,
  "tool_call_count": 0,
  "remaining_tool_calls": 10
}
```

L'actor decide che serve un tool read-only.
Non chiama MCP direttamente.
Produce invece un `ActorResult` con `ToolRequest`.

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

Stato aggiornato:

```json
{
  "current_state": "ready_tool_execution",
  "actor_turn_count": 1,
  "current_tool_request": {
    "tool_name": "mcp.local_test.text_stats",
    "text": "agent agent governance MCP evidence evidence evidence",
    "limit": 5
  }
}
```

## 5. Governance

Prima dell'esecuzione del tool, il runtime passa dalla governance.

Input di policy semplificato:

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

Output `confidence_risk_trajectory`:

```json
{
  "evidence_quality": {
    "score": 0.0,
    "level": "weak",
    "reason_codes": [
      "observed_evidence_missing",
      "source_locator_missing"
    ]
  },
  "operational_risk": {
    "score": 0.15,
    "level": "low",
    "reason_codes": [
      "tool_risk_low",
      "task_sensitivity_low",
      "read_only_capability"
    ]
  },
  "trajectory_health": {
    "score": 1.0,
    "level": "healthy",
    "reason_codes": [
      "actor_turns_within_limit",
      "no_failed_tool_results",
      "no_replan_needed"
    ]
  },
  "recommended_directive": "continue",
  "summary": "Evidence quality is weak (0.00), operational risk is low (0.15), and trajectory health is healthy (1.00)."
}
```

Decisione tipica dopo `decision_combiner`:

```json
{
  "policy_decision": "allow_read_only",
  "directive": "continue_read_only",
  "policy_directive": "continue_read_only",
  "oversight_recommended_directive": "continue",
  "oversight_summary": "Evidence quality is weak (0.00), operational risk is low (0.15), and trajectory health is healthy (1.00).",
  "reason_codes": ["read_only_allowed"],
  "summary": "Read-only tool use is allowed. Adaptive oversight recommended 'continue' and preserved the policy directive 'continue_read_only'.",
  "evaluated_tool_name": "mcp.local_test.text_stats"
}
```

Solo dopo questa decisione il grafo instrada verso `tool_gateway`.

## 6. Tool Gateway

Il Tool Gateway riceve la `ToolRequest`:

```json
{
  "tool_name": "mcp.local_test.text_stats",
  "reason": "The current step requires deterministic word frequency statistics.",
  "text": "agent agent governance MCP evidence evidence evidence",
  "limit": 5
}
```

Legge la definizione del tool da `configs/layer2_tool_gateway.json`:

```json
{
  "tool_name": "mcp.local_test.text_stats",
  "backend": "mcp",
  "capability": "read",
  "risk_level": "low",
  "enabled": true,
  "requires_human_review": false,
  "required_fields": ["text"],
  "mcp_server_id": "local_test",
  "mcp_tool_name": "text_stats"
}
```

Controlla anche il server MCP configurato:

```json
{
  "local_test": {
    "enabled": true,
    "command": "{python}",
    "args": ["{repo_root}\\tests\\fixtures\\mcp_local_test_server.py"],
    "allowed_tools": [
      "echo",
      "read_fixture",
      "text_stats",
      "calculate_expression"
    ]
  }
}
```

Validazioni eseguite:

```text
tool registrato: sì
tool abilitato: sì
backend MCP: sì
server MCP registrato: sì
server MCP abilitato: sì
mcp_tool_name allowlisted: sì
capability read: sì
required field text presente: sì
```

Il gateway costruisce gli argomenti MCP partendo solo dai campi tipizzati della `ToolRequest`:

```json
{
  "text": "agent agent governance MCP evidence evidence evidence",
  "limit": 5
}
```

## 7. Chiamata MCP

Il gateway chiama il server MCP locale configurato.

Richiesta concettuale:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "text_stats",
    "arguments": {
      "text": "agent agent governance MCP evidence evidence evidence",
      "limit": 5
    }
  }
}
```

Risposta concettuale:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"characters\": 53, \"lines\": 1, \"top_words\": [[\"evidence\", 3], [\"agent\", 2], [\"governance\", 1], [\"mcp\", 1]], \"words\": 7}"
      }
    ]
  }
}
```

## 8. Normalizzazione Tool Result

Il gateway non passa l'output MCP grezzo all'actor.
Lo converte in `ToolExecutionResult`.

```json
{
  "request": {
    "plan_step_id": "step_1",
    "tool_name": "mcp.local_test.text_stats",
    "reason": "The current step requires deterministic word frequency statistics.",
    "text": "agent agent governance MCP evidence evidence evidence",
    "limit": 5
  },
  "success": true,
  "summary": "MCP tool 'text_stats' executed successfully.",
  "content": "{\"characters\": 53, \"lines\": 1, \"top_words\": [[\"evidence\", 3], [\"agent\", 2], [\"governance\", 1], [\"mcp\", 1]], \"words\": 7}",
  "sources": [
    {
      "title": "mcp.local_test.text_stats",
      "locator": "mcp://local_test/text_stats",
      "snippet": "{\"characters\": 53, \"lines\": 1, \"top_words\": [[\"evidence\", 3], [\"agent\", 2], [\"governance\", 1], [\"mcp\", 1]], \"words\": 7}"
    }
  ],
  "error_message": null
}
```

Stato aggiornato:

```json
{
  "last_tool_result": {
    "success": true,
    "summary": "MCP tool 'text_stats' executed successfully."
  },
  "tool_call_count": 1,
  "current_tool_request": null,
  "current_state": "acting"
}
```

## 9. Observe Tool Result

Il nodo `observe_tool_result` converte il `ToolExecutionResult` in evidenza osservabile.

```json
{
  "observed_evidence": [
    {
      "evidence_id": "step_1:tool:1:source:1",
      "plan_step_id": "step_1",
      "title": "mcp.local_test.text_stats",
      "locator": "mcp://local_test/text_stats",
      "excerpt": "{\"characters\": 53, \"lines\": 1, \"top_words\": [[\"evidence\", 3], [\"agent\", 2], [\"governance\", 1], [\"mcp\", 1]], \"words\": 7}",
      "source_tool": "mcp.local_test.text_stats"
    }
  ]
}
```

Registra anche una scratchpad entry audit-safe:

```json
{
  "turn_index": 1,
  "plan_step_id": "step_1",
  "entry_type": "observation",
  "summary": "Observed tool result from 'mcp.local_test.text_stats': MCP tool 'text_stats' executed successfully.",
  "evidence_refs": [
    "step_1:tool:1:source:1"
  ]
}
```

Il grafo torna quindi all'actor.

## 10. Actor Dopo Evidenza

L'actor ora riceve evidenza e ultimo tool result.

```json
{
  "current_plan_step": {
    "step_id": "step_1",
    "objective": "Compute text statistics for the supplied text.",
    "status": "in_progress"
  },
  "observed_evidence": [
    {
      "evidence_id": "step_1:tool:1:source:1",
      "title": "mcp.local_test.text_stats",
      "excerpt": "{\"characters\": 53, \"lines\": 1, \"top_words\": [[\"evidence\", 3], [\"agent\", 2], [\"governance\", 1], [\"mcp\", 1]], \"words\": 7}"
    }
  ],
  "last_tool_result": {
    "success": true,
    "content": "{\"characters\": 53, \"lines\": 1, \"top_words\": [[\"evidence\", 3], [\"agent\", 2], [\"governance\", 1], [\"mcp\", 1]], \"words\": 7}"
  }
}
```

L'actor completa il primo step:

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

Il runtime registra lo step completato:

```json
{
  "executed_steps": [
    {
      "step_id": "step_1",
      "objective": "Compute text statistics for the supplied text.",
      "result_type": "intermediate_output",
      "summary": "Text statistics were computed successfully.",
      "output_text": "The most frequent word is evidence, followed by agent."
    }
  ],
  "plan_steps": [
    {
      "step_id": "step_1",
      "status": "completed"
    },
    {
      "step_id": "step_2",
      "status": "in_progress"
    }
  ],
  "current_plan_step_index": 1
}
```

## 11. Governance Sullo Step Completato

Il secondo passaggio in governance valuta un output locale, non una tool call.

```json
{
  "actor_result_type": "intermediate_output",
  "capability_mode": "none",
  "external_impact": false,
  "tool_name": null,
  "observed_evidence_count": 1,
  "current_plan_step_id": "step_1"
}
```

Output `confidence_risk_trajectory`:

```json
{
  "evidence_quality": {
    "score": 0.98,
    "level": "strong",
    "reason_codes": [
      "evidence_marked_sufficient",
      "objective_marked_satisfied",
      "actor_confidence_contributed",
      "observed_evidence_present",
      "source_locator_present",
      "last_tool_success"
    ]
  },
  "operational_risk": {
    "score": 0.15,
    "level": "low"
  },
  "trajectory_health": {
    "score": 1.0,
    "level": "healthy"
  },
  "recommended_directive": "continue",
  "summary": "Evidence quality is strong (0.98), operational risk is low (0.15), and trajectory health is healthy (1.00)."
}
```

Decisione tipica dopo `decision_combiner`:

```json
{
  "policy_decision": "allow",
  "directive": "continue",
  "policy_directive": "continue",
  "oversight_recommended_directive": "continue",
  "reason_codes": ["local_output_allowed"],
  "summary": "Local-only output is allowed. Adaptive oversight recommended 'continue' and preserved the policy directive 'continue'."
}
```

Il grafo torna all'actor per lo step finale.

## 12. Actor Finale

Current step:

```json
{
  "step_id": "step_2",
  "objective": "Synthesize the final answer from the computed statistics.",
  "status": "in_progress"
}
```

Output finale:

```json
{
  "plan_step_id": "step_2",
  "result_type": "final_output",
  "summary": "Final answer produced from MCP text statistics.",
  "capability_mode": "none",
  "external_impact": false,
  "output_text": "Nel testo fornito, la parola più frequente è 'evidence' con 3 occorrenze. Segue 'agent' con 2 occorrenze. Le parole 'governance' e 'MCP' compaiono una volta ciascuna.",
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

Stato aggiornato:

```json
{
  "final_output_text": "Nel testo fornito, la parola più frequente è 'evidence' con 3 occorrenze. Segue 'agent' con 2 occorrenze. Le parole 'governance' e 'MCP' compaiono una volta ciascuna.",
  "current_plan_step_index": null,
  "current_state": "completed"
}
```

## 13. Governance Finale E Finalize

La governance finale valuta l'output finale locale.

```json
{
  "actor_result_type": "final_output",
  "capability_mode": "none",
  "external_impact": false,
  "tool_name": null,
  "observed_evidence_count": 1,
  "current_plan_step_id": "step_2"
}
```

Decisione:

```json
{
  "policy_decision": "allow",
  "directive": "continue",
  "policy_directive": "continue",
  "oversight_recommended_directive": "continue",
  "oversight_summary": "Evidence quality is strong (0.98), operational risk is low (0.15), and trajectory health is healthy (1.00).",
  "summary": "Final local output is allowed. Adaptive oversight recommended 'continue' and preserved the policy directive 'continue'."
}
```

Il grafo arriva a `finalize_bootstrap` e termina.

## Stato Finale

`TaskState` finale semplificato:

```json
{
  "current_state": "completed",
  "bootstrap_completed": true,
  "tool_call_count": 1,
  "actor_turn_count": 3,
  "final_output_text": "Nel testo fornito, la parola più frequente è 'evidence' con 3 occorrenze. Segue 'agent' con 2 occorrenze. Le parole 'governance' e 'MCP' compaiono una volta ciascuna.",
  "plan_steps": [
    {
      "step_id": "step_1",
      "status": "completed"
    },
    {
      "step_id": "step_2",
      "status": "completed"
    }
  ],
  "observed_evidence": [
    {
      "evidence_id": "step_1:tool:1:source:1",
      "title": "mcp.local_test.text_stats",
      "locator": "mcp://local_test/text_stats",
      "source_tool": "mcp.local_test.text_stats"
    }
  ],
  "governance_history": [
    {
      "policy_decision": "allow_read_only",
      "directive": "continue_read_only",
      "evaluated_tool_name": "mcp.local_test.text_stats"
    },
    {
      "policy_decision": "allow",
      "directive": "continue"
    },
    {
      "policy_decision": "allow",
      "directive": "continue"
    }
  ],
  "oversight_history": [
    {
      "evidence_quality": {"score": 0.0, "level": "weak"},
      "operational_risk": {"score": 0.15, "level": "low"},
      "trajectory_health": {"score": 1.0, "level": "healthy"},
      "recommended_directive": "continue"
    },
    {
      "evidence_quality": {"score": 0.98, "level": "strong"},
      "operational_risk": {"score": 0.15, "level": "low"},
      "trajectory_health": {"score": 1.0, "level": "healthy"},
      "recommended_directive": "continue"
    }
  ]
}
```

## Runtime Trace Export

Alla fine della run, `src/layer2_runtime_governance/trace.py` costruisce un artefatto tipizzato a partire da `TaskState`.
Il trace non Ã¨ un secondo stato runtime: Ã¨ una vista read-only e JSON-safe per debug, tesi, valutazione futura e payload HITL.

Forma semplificata:

```json
{
  "runtime": {
    "thread_id": "layer2-demo",
    "current_state": "completed",
    "bootstrap_completed": true,
    "actor_turn_count": 3,
    "tool_call_count": 1
  },
  "intake": {
    "goal": "Analizza questo testo...",
    "domain_type": "analysis",
    "sensitivity": "low",
    "policy_profile_name": "default"
  },
  "plan_steps": [
    {
      "step_id": "step_1",
      "objective": "Compute word frequency statistics.",
      "status": "completed"
    }
  ],
  "tool_calls": [
    {
      "tool_name": "mcp.local_test.text_stats",
      "success": true,
      "summary": "Computed text statistics.",
      "sources": [
        {
          "locator": "mcp://local_test/text_stats"
        }
      ]
    }
  ],
  "observed_evidence": [
    {
      "evidence_id": "step_1:tool:1:source:1",
      "locator": "mcp://local_test/text_stats",
      "source_tool": "mcp.local_test.text_stats"
    }
  ],
  "governance_decisions": [
    {
      "policy_decision": "allow_read_only",
      "policy_directive": "continue_read_only",
      "oversight_recommended_directive": "continue",
      "directive": "continue_read_only"
    }
  ],
  "oversight_assessments": [
    {
      "evidence_quality": {"score": 0.98, "level": "strong"},
      "operational_risk": {"score": 0.15, "level": "low"},
      "trajectory_health": {"score": 1.0, "level": "healthy"},
      "recommended_directive": "continue"
    }
  ],
  "execution": {
    "final_output_text": "Nel testo fornito...",
    "scratchpad": [],
    "step_validations": []
  }
}
```

`src/main_layer2.py --full-trace` stampa questo trace completo.
Il compact trace della CLI resta disponibile, ma viene derivato dallo stesso exporter tipizzato.

## Sintesi Architetturale

Il flusso dimostra la distinzione centrale della tesi:

```text
Autonomia esterna:
  l'utente assegna un task e riceve un risultato.

Governance interna:
  ogni uso di capacità esterne passa da contratti tipizzati,
  OPA policy, oversight deterministico, Tool Gateway,
  normalizzazione evidenze e trace.
```

Formula sintetica:

```text
User task
  -> Layer 1 Intake
  -> TaskState
  -> Planner
  -> Actor
  -> Governance
     -> OPA policy
     -> confidence_risk_trajectory
     -> decision_combiner
  -> Tool Gateway
  -> MCP backend
  -> ToolExecutionResult
  -> ObservedEvidence
  -> Actor
  -> Final output
```
