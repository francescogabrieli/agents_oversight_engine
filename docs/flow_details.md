# Dettaglio Del Flusso Runtime

Questo documento descrive il flusso attuale del repository a livello debug.
Non è la vista user-facing. Mostra cosa succede internamente tra Layer 1, Layer 2, governance, HITL, Tool Gateway, export della trace e persistenza locale del runtime.

## Task Di Esempio

Input:

```json
{
  "raw_task_text": "Analizza questo testo e dimmi quali parole ricorrono di piu: 'agent agent governance MCP evidence evidence evidence'.",
  "context_hint": "Usa strumenti read-only se utili."
}
```

Risultato atteso lato utente:

```text
Nel testo fornito, la parola piu frequente e "evidence" con 3 occorrenze.
Segue "agent" con 2 occorrenze.
Le parole "governance" e "MCP" compaiono una volta ciascuna.
```

L’utente non vede gli internals del planner, gli score di governance, il matching storico behavioural, il ragionamento del judge, i dettagli del Tool Gateway o il trasporto MCP.
Queste parti sono interne ed esistono per controllo, evidenza, auditabilità e debugging.

## Forma Attuale Del Runtime

La slice attuale di Layer 2 è:

```text
START
  -> bootstrap_from_intake
  -> planner
  -> actor
  -> governance
  -> { actor | tool_gateway | replanner | human_review | finalize_bootstrap }
tool_gateway
  -> observe_tool_result
  -> actor
replanner
  -> actor
human_review
  -> { planner | actor | tool_gateway | replanner | finalize_bootstrap }
finalize_bootstrap
  -> END
```

Punti importanti:

- l’output del planner è semantico e tipizzato
- l’output dell’actor è tipizzato e limitato allo step corrente
- l’uso dei tool è runtime-bound e passa attraverso il Tool Gateway
- la governance è il punto di controllo hard dopo ogni actor turn
- la governance oggi combina cinque layer espliciti:
  - static policy
  - dynamic policy
  - behavioural intelligence
  - selective judge review
  - deterministic decision combiner
- dynamic policy, behavioural e judge possono solo rendere la decisione più conservativa
- HITL usa `interrupt / resume` tipizzato
- i checkpoint LangGraph sono persistiti su disco tramite un saver locale durevole
- gli artifact runtime sono persistiti sotto `artifacts/runtime/<thread_id>/`
- la memoria behavioural è persistita in `artifacts/runtime/behavioral/behavioral.sqlite3`
- `external_communication` ha un modello a fasi tipizzato dentro `TaskState`

## 1. Intake Del Layer 1

Layer 1 converte la task grezza in un `IntakeResult` canonico.
Dopo questo punto, il runtime non lavora più direttamente sul testo libero dell’utente.

Payload concettuale di intake:

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

Questo è il vero contratto tra Layer 1 e Layer 2.

## 2. Bootstrap Dentro Layer 2

`bootstrap_from_intake` converte `IntakeResult` in `TaskState`.
Qui vengono inizializzati stato runtime, cursore del piano, contatori, storici di governance, bookkeeping HITL, history behavioural e judge, e il branch tipizzato di external communication quando rilevante.

Stato concettuale di bootstrap:

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
  "static_policy_history": [],
  "dynamic_policy_history": [],
  "behavioral_history": [],
  "judge_history": [],
  "governance_history": [],
  "human_review_history": [],
  "external_communication": null,
  "runtime_flags": [],
  "tool_call_count": 0,
  "actor_turn_count": 0
}
```

A questo punto il runtime non ha ancora pianificato né agito.

## 3. Planner

Il planner produce un piano semantico.
Non emette tool call concrete né payload eseguibili.

Esempio di piano:

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
  "planner_prompt_version": "v1"
}
```

Il planner può compattare piani source-summary troppo decomposi, ma il risultato resta semantico.

## 4. Actor

L’actor riceve il piano completo come contesto, ma può agire solo sullo step corrente.
Può:

- proporre una tool request
- produrre output locale
- chiedere chiarimento
- segnalare replan
- completare lo step quando l’evidenza e sufficiente

Esempio di output actor per il primo step:

```json
{
  "plan_step_id": "step_1",
  "result_type": "propose_action",
  "summary": "Request read-only MCP text statistics for the supplied text.",
  "capability_mode": "read_only",
  "external_impact": false,
  "output_text": null,
  "blocking_reason": null,
  "tool_request": {
    "plan_step_id": "step_1",
    "tool_name": "mcp.local_test.text_stats",
    "reason": "The current step requires deterministic word frequency statistics.",
    "query": null,
    "url": null,
    "text": "agent agent governance MCP evidence evidence evidence",
    "limit": 5
  },
  "step_validation": null,
  "scratchpad_entry": {
    "turn_index": 1,
    "plan_step_id": "step_1",
    "entry_type": "tool_decision",
    "summary": "Text statistics are needed to satisfy the current step."
  }
}
```

A questo punto il runtime non esegue ancora il tool.
L’outcome dell’actor passa sempre prima dalla governance.

## 5. Snapshot Di Input Della Governance

Prima che venga presa una decisione, la governance costruisce un `PolicyEvaluationInput` compatto.
Questo è lo snapshot canonico inviato ai layer di policy.

Esempio di input governance:

```json
{
  "task_domain_type": "analysis",
  "task_domain_label": "text_analysis",
  "task_sensitivity": "low",
  "policy_profile_name": "default",
  "confidence_threshold": 0.65,
  "read_only_mode": false,
  "budget_max_steps": 5,
  "budget_max_tool_calls": 10,
  "runtime_state": "ready_tool_execution",
  "runtime_flags": [],
  "current_plan_step_id": "step_1",
  "current_plan_step_objective": "Compute text statistics for the supplied text.",
  "actor_result_type": "propose_action",
  "capability_mode": "read_only",
  "external_impact": false,
  "blocking_reason": null,
  "actor_has_output_text": false,
  "tool_name": "mcp.local_test.text_stats",
  "tool_has_query": false,
  "tool_has_url": false,
  "tool_call_count": 0,
  "observed_evidence_count": 0,
  "actor_turn_count": 1,
  "external_communication_phase": null
}
```

Questo è il confine comune di input per tutto lo stack di governance.

## 6. Static Policy

Il primo layer di governance è la static policy.
È supportato da OPA via HTTP e decide i vincoli hard e deterministici.

Esempio di static policy assessment:

```json
{
  "policy_decision": "allow_read_only",
  "reason_codes": [
    "read_only_allowed"
  ],
  "summary": "Read-only tool use is allowed for this analysis task.",
  "policy_engine_name": "opa_http_policy_engine",
  "policy_bundle_version": "layer2_governance_v1",
  "policy_directive": "continue_read_only"
}
```

Le possibili decisioni statiche sono:

- `allow`
- `allow_read_only`
- `ask_clarification`
- `require_human_review`
- `deny`

Se la static policy restituisce `deny`, il runtime si ferma.
Se restituisce `require_human_review` o `ask_clarification`, il runtime va in pausa per HITL.

## 7. Dynamic Policy

Il secondo layer di governance è la dynamic policy deterministica.
Vive in `oversight.py` e calcola:

- `evidence_quality`
- `operational_risk`
- `trajectory_health`

Esempio di dynamic assessment per il primo turn:

```json
{
  "evidence_quality_score": 0.15,
  "evidence_quality_level": "weak",
  "operational_risk_score": 0.08,
  "operational_risk_level": "low",
  "trajectory_health_score": 1.0,
  "trajectory_health_level": "healthy",
  "recommended_directive": "continue_read_only",
  "summary": "Evidence is still weak because no observation exists yet, but the request is read-only and trajectory health is good.",
  "reason_codes": [
    "low_evidence_early_turn",
    "low_operational_risk",
    "healthy_trajectory"
  ]
}
```

La dynamic policy può solo preservare o irrigidire la direttiva statica.
Non può rendere il runtime più permissivo della static policy.

## 8. Layer Behavioural

Il terzo layer di governance è l’intelligenza behavioural.
Legge lo store SQLite locale e confronta la run corrente con run storiche.

Il layer behavioural può calcolare:

- `matched_run_count`
- `historical_similarity_score`
- `behavior_deviation_score`
- `novelty_score`
- `behavioral_flags`
- `behavioral_recommended_directive`

Esempio di behavioural assessment per questa task di text analysis:

```json
{
  "history_available": true,
  "historical_run_count": 42,
  "feature_snapshot_version": "behavioral_features_v2",
  "matched_run_count": 5,
  "matched_run_ids": [
    "layer2-7f4d",
    "layer2-b9c2",
    "layer2-a1d0"
  ],
  "historical_similarity_score": 0.88,
  "behavior_deviation_score": 0.12,
  "novelty_score": 0.09,
  "behavioral_recommended_directive": "continue",
  "behavioral_flags": [],
  "summary": "Current run closely matches prior low-risk analysis runs and does not show abnormal trajectory behavior.",
  "notes": [
    "strict_candidate_family=analysis|low|default",
    "top_similarity_cluster_stable=true"
  ]
}
```

Il behavioural è solo conservativo.
Può raccomandare:

- `continue`
- `request_more_evidence`
- `replan`
- `wait_human_review`

Non raccomanda mai `stop`.

## 9. Judge Selettivo

Il quarto layer di governance è il judge semantico selettivo.
Non gira a ogni turn.

Il judge viene saltato quando la static policy ha già restituito:

- `deny`
- `require_human_review`
- `ask_clarification`

Altrimenti può girare quando la situazione appare fragile, per esempio:

- la dynamic policy e gia conservativa
- il behavioural non è `continue`
- l’actor sta tentando una completion borderline
- c’è mismatch tra evidenza e output
- la task si trova in una fase rischiosa di draft external-communication

Esempio di judge assessment per la prima tool proposal di questo caso:

```json
{
  "invoked": false,
  "recommendation": "continue",
  "confidence": 0.0,
  "summary": "Judge not invoked because no semantic risk signal required qualitative review at this turn.",
  "rationale": "",
  "flags": [],
  "evaluated_target": "actor_result"
}
```

Il caso più interessante è un fragile completion attempt.
Per esempio, se l’actor più avanti prova ad affermare una summary grounded sul DMA avendo solo evidenza di tipo source-discovery, il judge può produrre:

```json
{
  "invoked": true,
  "recommendation": "request_more_evidence",
  "confidence": 0.78,
  "summary": "Observed evidence is only metadata or source discovery, so the completion attempt is more specific than the available support.",
  "rationale": "The actor is trying to complete a summary-style step, but the available evidence does not contain extracted substantive content strong enough to support that precision safely.",
  "flags": [
    "weak_evidence_support",
    "source_metadata_only",
    "goal_evidence_mismatch",
    "borderline_completion",
    "semantic_precision_risk"
  ],
  "evaluated_target": "actor_result"
}
```

Come behavioural e dynamic policy, anche il judge può solo preservare o irrigidire la decisione.

## 10. Decision Combiner

L’ultimo layer di governance è il decision combiner deterministico.
Unisce:

- static policy
- dynamic policy
- behavioural
- judge

La precedence è:

1. `deny -> stop`
2. `require_human_review -> wait_human_review`
3. `ask_clarification -> ask_user_clarification`
4. altrimenti prende la direttiva più conservativa tra:
   - direttiva della static policy
   - raccomandazione dynamic
   - raccomandazione behavioural
   - raccomandazione del judge

Esempio di combined governance decision per la prima tool request:

```json
{
  "directive": "continue_read_only",
  "policy_decision": "allow_read_only",
  "reason_codes": [
    "read_only_allowed"
  ],
  "summary": "Read-only tool use is allowed for this analysis task. Dynamic policy preserved the static directive 'continue_read_only'. Current run closely matches prior low-risk analysis runs and does not show abnormal trajectory behavior.",
  "constrains_capability_use": true,
  "evaluated_actor_result_type": "propose_action",
  "evaluated_tool_name": "mcp.local_test.text_stats",
  "policy_engine_name": "opa_http_policy_engine",
  "policy_bundle_version": "layer2_governance_v1",
  "policy_directive": "continue_read_only",
  "oversight_recommended_directive": "continue_read_only",
  "behavioral_recommended_directive": "continue",
  "judge_recommended_directive": "continue",
  "judge_invoked": false,
  "static_policy_summary": "Read-only tool use is allowed for this analysis task.",
  "dynamic_policy_summary": "Evidence is still weak because no observation exists yet, but the request is read-only and trajectory health is good.",
  "behavioral_summary": "Current run closely matches prior low-risk analysis runs and does not show abnormal trajectory behavior.",
  "judge_summary": "Judge not invoked because no semantic risk signal required qualitative review at this turn.",
  "behavioral_similarity_score": 0.88,
  "behavioral_deviation_score": 0.12,
  "behavioral_novelty_score": 0.09,
  "oversight_summary": "Evidence is still weak because no observation exists yet, but the request is read-only and trajectory health is good."
}
```

Questo è il record canonico di governance salvato in `TaskState`.

## 11. Tool Gateway

Quando la governance consente la request, il runtime entra in `READY_TOOL_EXECUTION` e invia la tool request al Tool Gateway.
L’actor non chiama mai MCP direttamente.

Il gateway:

- controlla la configurazione del registry
- valida la capability del tool
- mappa i campi tipizzati della request negli argomenti backend
- normalizza i risultati backend in `ToolExecutionResult`

Esempio di request MCP normalizzata:

```json
{
  "tool_name": "mcp.local_test.text_stats",
  "reason": "The current step requires deterministic word frequency statistics.",
  "query": null,
  "url": null,
  "text": "agent agent governance MCP evidence evidence evidence",
  "limit": 5
}
```

Esempio di tool result normalizzato:

```json
{
  "request": {
    "tool_name": "mcp.local_test.text_stats",
    "plan_step_id": "step_1"
  },
  "success": true,
  "summary": "Text statistics computed successfully.",
  "content": "{\"characters\":53,\"lines\":1,\"top_words\":[[\"evidence\",3],[\"agent\",2],[\"governance\",1],[\"mcp\",1]],\"words\":7}",
  "error_message": null,
  "sources": [
    {
      "title": "mcp.local_test.text_stats",
      "locator": "mcp://local_test/text_stats",
      "snippet": "{\"characters\":53,\"lines\":1,\"top_words\":[[\"evidence\",3],[\"agent\",2],[\"governance\",1],[\"mcp\",1]],\"words\":7}"
    }
  ]
}
```

## 12. Observe Tool Result

`observe_tool_result` converte il tool result in evidenza esplicita.
Aggiunge anche scratchpad entries audit-safe.

Esempio di evidenza:

```json
{
  "evidence_id": "step_1:tool:1:source:1",
  "plan_step_id": "step_1",
  "title": "mcp.local_test.text_stats",
  "locator": "mcp://local_test/text_stats",
  "excerpt": "{\"characters\":53,\"lines\":1,\"top_words\":[[\"evidence\",3],[\"agent\",2],[\"governance\",1],[\"mcp\",1]],\"words\":7}",
  "source_tool": "mcp.local_test.text_stats",
  "support_level": "detailed_extracted"
}
```

Esempio di scratchpad observation entry:

```json
{
  "turn_index": 1,
  "plan_step_id": "step_1",
  "entry_type": "observation",
  "summary": "Observed tool result from 'mcp.local_test.text_stats': Text statistics computed successfully.",
  "evidence_refs": [
    "step_1:tool:1:source:1"
  ]
}
```

Il grafo poi torna all’actor con la nuova evidenza allegata.

## 13. L’Actor Completa Lo Step

L’actor vede l’evidenza e produce un risultato di step validato.

Esempio di output validato:

```json
{
  "plan_step_id": "step_1",
  "result_type": "intermediate_output",
  "summary": "Text statistics were computed successfully.",
  "capability_mode": "none",
  "external_impact": false,
  "output_text": "The most frequent word is evidence, followed by agent.",
  "tool_request": null,
  "step_validation": {
    "plan_step_id": "step_1",
    "objective_satisfied": true,
    "evidence_sufficient": true,
    "confidence": 0.9,
    "next_action": "complete_step",
    "rationale_summary": "The observed MCP statistics satisfy the current step objective."
  }
}
```

Il runtime marca lo step come completato e avanza il cursore del piano.

## 14. Step Finale

L’actor esegue lo step di sintesi ed emette l’output finale.

Esempio di output finale:

```json
{
  "plan_step_id": "step_2",
  "result_type": "final_output",
  "summary": "Final answer produced from MCP text statistics.",
  "capability_mode": "none",
  "external_impact": false,
  "output_text": "Nel testo fornito, la parola piu frequente e 'evidence' con 3 occorrenze. Segue 'agent' con 2 occorrenze. Le parole 'governance' e 'MCP' compaiono una volta ciascuna.",
  "tool_request": null,
  "step_validation": {
    "plan_step_id": "step_2",
    "objective_satisfied": true,
    "evidence_sufficient": true,
    "confidence": 0.92,
    "next_action": "complete_step",
    "rationale_summary": "The final answer is grounded in the observed MCP text statistics."
  }
}
```

Se la governance accetta questo output locale, il runtime arriva infine a `finalize_bootstrap` e termina.

## 15. Esempio Di Escalation Del Judge Su Una Completion Fragile

Il path del judge si capisce meglio su una task di ricerca che non sul toy example di text-analysis sopra.

Supponiamo che l’actor stia lavorando su:

```json
{
  "goal": "Find official sources about the Digital Markets Act and produce a short summary."
}
```

Supponiamo che l’evidenza osservata sia solo:

```json
{
  "title": "European Commission DMA page",
  "locator": "https://example.eu/dma",
  "excerpt": "Official DMA page discovered.",
  "support_level": "source_discovery"
}
```

Se l’actor tenta comunque:

```json
{
  "result_type": "final_output",
  "output_text": "The Digital Markets Act entered into force on a specific date and imposes obligations on gatekeepers ..."
}
```

allora la governance può apparire così:

```json
{
  "static_policy": {
    "policy_decision": "allow",
    "policy_directive": "continue"
  },
  "dynamic_policy": {
    "recommended_directive": "request_more_evidence",
    "evidence_quality_level": "weak"
  },
  "behavioral": {
    "behavioral_recommended_directive": "continue",
    "historical_similarity_score": 0.61
  },
  "judge": {
    "invoked": true,
    "recommendation": "request_more_evidence",
    "confidence": 0.78,
    "flags": [
      "weak_evidence_support",
      "source_metadata_only",
      "goal_evidence_mismatch",
      "borderline_completion",
      "semantic_precision_risk"
    ]
  },
  "combined_decision": {
    "directive": "request_more_evidence"
  }
}
```

Questo è il comportamento conservativo atteso:
il runtime non lascia passare una completion quando l’evidenza è più debole della precisione dell’output.

## 16. Branch HITL

Se la governance instrada verso `WAITING_HUMAN_REVIEW`, il runtime va in pausa ed espone una human review request tipizzata.

Il contratto attivo è:

- `governance` decide se la pausa è richiesta
- dynamic policy, behavioural e judge possono tutti escalare verso review
- `human_review` riprende tramite decisioni tipizzate

Esempio di human review request snapshot:

```json
{
  "request_id": "review-8f2c1a",
  "thread_id": "layer2-1234",
  "current_state": "waiting_human_review",
  "resume_state": "acting",
  "task_goal": "Find official sources about the Digital Markets Act and produce a short summary.",
  "current_plan_step_id": "step_2",
  "governance": {
    "directive": "wait_human_review",
    "policy_decision": "allow",
    "policy_directive": "continue",
    "oversight_recommended_directive": "request_more_evidence",
    "behavioral_recommended_directive": "wait_human_review",
    "judge_recommended_directive": "wait_human_review",
    "judge_invoked": true
  },
  "summaries": {
    "static_policy_summary": "Local output is allowed in principle.",
    "dynamic_policy_summary": "Evidence is weaker than the output precision being attempted.",
    "behavioral_summary": "This run deviates strongly from similar prior runs and similar runs often ended in review.",
    "judge_summary": "The proposed completion overclaims beyond the available evidence support."
  }
}
```

Le decisioni canoniche di human review sono:

- `approve_continue`
- `approve_continue_read_only`
- `request_more_evidence`
- `replan`
- `ask_clarification`
- `abort`

La dashboard renderizza questo stesso contratto.
Non è un secondo modello runtime.

## 17. Persistenza E Runtime Artifacts

Il runtime persiste più di un semplice checkpoint process-local.

Le superfici attuali di persistenza sono:

- LangGraph checkpoint store durevole su disco
- full runtime trace JSON
- compact runtime trace JSON
- dashboard snapshot JSON
- pending HITL payload JSON quando una review e attiva
- runtime summary JSON
- behavioural SQLite read model

Posizioni di default degli artifact:

```text
artifacts/runtime/<thread_id>/
artifacts/runtime/behavioral/behavioral.sqlite3
```

Lo store SQLite behavioural mantiene dati storici strutturati come:

- `runs`
- `steps`
- `tool_calls`
- `governance_decisions`
- `dynamic_signals`
- `behavioral_features`
- `judge_assessments`

## 18. Export Della Trace

A fine run, `trace.py` costruisce una trace tipizzata e JSON-safe a partire da `TaskState`.
La trace è read-only. Non è un secondo runtime state.

La trace esportata oggi include sezioni separate per:

- `static_policy_assessments`
- `dynamic_policy_assessments`
- `behavioral_assessments`
- `judge_assessments`
- `governance_decisions`

Estratto concettuale:

```json
{
  "thread_id": "layer2-1234",
  "runtime": {
    "current_state": "completed",
    "actor_turn_count": 3,
    "tool_call_count": 1
  },
  "behavioral_assessments": [
    {
      "historical_similarity_score": 0.88,
      "behavior_deviation_score": 0.12,
      "novelty_score": 0.09,
      "behavioral_recommended_directive": "continue"
    }
  ],
  "judge_assessments": [
    {
      "invoked": false,
      "recommendation": "continue"
    }
  ],
  "governance_decisions": [
    {
      "directive": "continue_read_only",
      "policy_directive": "continue_read_only",
      "behavioral_recommended_directive": "continue",
      "judge_recommended_directive": "continue"
    }
  ]
}
```

Viene usata per:

- debug output
- evidenza per la tesi
- payload dashboard
- evaluation offline
- artifact runtime persistiti per replay e ispezione

## 19. Branch External Communication

`external_communication` è un vero subflow tipizzato dentro `TaskState`.
Il runtime attuale traccia fasi come:

- `evidence_collection`
- `draft_proposal`
- `human_review_required`
- `approved_for_execution`
- `blocked`

Esempio di snapshot:

```json
{
  "phase": "draft_proposal",
  "last_transition_summary": "A local draft proposal exists and remains inside the runtime boundary."
}
```

Questo non significa ancora che l’esecuzione esterna con impatto reale sia pienamente implementata.
Significa che il runtime sa già distinguere:

- raccolta evidenze
- creazione di draft locale
- gate di review obbligatorio
- stato approved-but-not-yet-executed

Questa fase è visibile in:

- `TaskState`
- dashboard snapshot
- trace esportata
- runtime artifacts
- governance input

## Lettura Attuale Per La Tesi

L’architettura attuale dimostra questa separazione:

```text
User task
  -> Layer 1 Intake
  -> IntakeResult
  -> TaskState
  -> Planner
  -> Actor
  -> Governance
     -> static policy
     -> dynamic policy
     -> behavioural intelligence
     -> selective judge
     -> deterministic decision combiner
  -> Tool Gateway
  -> MCP backend or other configured backend
  -> ToolExecutionResult
  -> ObservedEvidence
  -> Actor
  -> HITL if needed
  -> Final output
  -> Trace export
  -> Persisted runtime artifacts
  -> Dashboard snapshot
  -> Behavioural SQLite read model
```

Il vincolo importante è che dashboard, trace e memoria behavioural sono tutti derivati dallo stesso runtime contract.
Non devono diventare fonti di verità parallele.
