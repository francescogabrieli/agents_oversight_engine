# ARCHITECTURE

## 1. TASK & CONTEXT INTAKE

### 1.1 Input
Richiesta utente / task generico da svolgere.

### 1.2 Operazioni Interne
LLM leggero oppure parser che trasforma il task generico in un JSON strutturato.

Per il PoC conviene separare:
- `task_spec`: descrizione del task
- `execution_policy`: parametri di esecuzione decisi dal sistema / supervisore

Dal task si estraggono goal, contesto, vincoli e alcune informazioni utili al controllo.
Soglia di confidenza e budget non vengono inferiti dal task, ma impostati come configurazione di esecuzione.

#### Esempio
```json
{
  "task_spec": {
    "goal": "...",
    "context": "...",
    "constraints": [],
    "domain": "generic_research",
    "sensitivity": "medium",
    "success_criteria": [
      "use at least 2 relevant sources",
      "produce a concise final answer"
    ]
  },
  "execution_policy": {
    "confidence_threshold": 0.65,
    "budget": {
      "max_steps": 5
    }
  }
}
```

### 1.3 Output
Task JSON strutturato, con separazione tra specifica del task e policy di esecuzione.

### 1.4 Framework / Librerie
Prompt + LLM API, parser Python, Pydantic.

## 2. AGENT RUNTIME / ORCHESTRATOR

### 2.1 Input
JSON strutturato del task.

### 2.2 Operazioni Interne
Motore agente che avvia l'esecuzione del task, pianifica i passi, decide il prossimo step, aggiorna lo stato corrente e chiede eventualmente un tool.

Per il PoC questo layer può essere pensato come il controller del ciclo di vita del task.
Mantiene uno stato unico del task (`TaskState`) e rende sempre visibile in che fase si trova l'agente.

#### Stati Possibili
- `planning`
- `acting`
- `observing`
- `replanning`
- `waiting_human_review`
- `completed`
- `aborted`

#### Informazioni Minime del `TaskState`
- stato corrente
- step corrente
- history dei passi eseguiti
- evidenze raccolte
- ultimo risultato di un tool
- motivazione sintetica della scelta corrente

#### Esempio
```json
{
  "status": "acting",
  "current_step": "search_sources",
  "step_history": [
    "parse_task",
    "plan_search"
  ],
  "action_type": "tool_call",
  "requested_tool": "search",
  "tool_input": {
    "query": "official sources about X"
  },
  "reasoning_note": "need external evidence before proceeding"
}
```

### 2.3 Output
Next step / tool request / stato aggiornato / output finale.

## 3. TOOL BUS

### 3.1 Input
Richiesta di tool call dal runtime.

#### Esempio
```json
{
  "tool_name": "search",
  "tool_input": {
    "query": "official sources about X"
  }
}
```

### 3.2 Operazioni Interne
Controlla se il tool esiste, se è consentito, che tipo di tool è (`read` o `write`), il livello di rischio, valida l'input, esegue la chiamata e formatta la risposta.

Per il PoC conviene arricchire il registry dei tool con pochi metadati utili al controllo.

#### Metadati Minimi del Registry
- `side_effects`: effetti attesi del tool
- `reversible`: se l'azione è reversibile
- `idempotent`: se ripetere la chiamata produce lo stesso effetto
- `input_schema` / `output_schema`: struttura attesa di input e output
- `retry_policy`: se e quante volte si può ritentare
- `data_sensitivity`: sensibilità dei dati coinvolti
- `supports_dry_run`: se il tool può produrre una preview senza eseguire davvero l'azione

Per i tool `write`, nel PoC è utile prevedere prima una modalità simulata o di preview, così da poter passare a HITL una proposta leggibile e verificabile prima dell'esecuzione reale.

#### Esempio di Registry
```json
TOOLS = {
    "search": {
        "type": "read",
        "risk": "low",
        "side_effects": [],
        "reversible": true,
        "idempotent": true,
        "data_sensitivity": "low",
        "supports_dry_run": false
    },
    "send_email": {
        "type": "write",
        "risk": "critical",
        "side_effects": ["external_communication"],
        "reversible": false,
        "idempotent": false,
        "data_sensitivity": "medium",
        "supports_dry_run": true
    }
}
```

> [!NOTE]
> Definire livelli di rischio appropriati per ciascun tool.

### 3.3 Output
Risultato strutturato del tool, con stato, payload e metadati essenziali per esecuzione o review.

#### Esempio di Output: Esecuzione Consentita
```json
{
  "status": "success",
  "tool_name": "search",
  "action_type": "read",
  "risk_level": "low",
  "payload": {
    "results": ["..."]
  },
  "requires_review": false
}
```

#### Esempio di Output: Review Richiesta
```json
{
  "status": "needs_review",
  "tool_name": "send_email",
  "action_type": "write",
  "risk_level": "critical",
  "side_effects": ["external_communication"],
  "payload": {
    "proposed_action": "send_email",
    "preview": {
      "recipient": "user@example.com",
      "subject": "Summary of requested task",
      "body": "..."
    }
  },
  "review_reason": "write action with external side effects",
  "requires_review": true
}
```

### 3.4 Framework / Librerie
Wrapper Python, registry custom, Pydantic, tool system del framework scelto.

## 4. OVERSIGHT CORE

### 4.1 Input
Per il PoC conviene alimentare l'oversight core con uno snapshot unico del task, invece che con oggetti sparsi.

Lo snapshot può includere:
- task e contesto iniziale
- stato corrente del task
- history dei passi
- evidenze raccolte
- ultimo tool result
- azione proposta dall'agente
- tool già usati
- eventuali flag di rischio
- eventuali approvazioni umane già date

#### Esempio
```json
{
  "task_id": "task_001",
  "task_spec": {
    "goal": "...",
    "context": "..."
  },
  "status": "waiting_human_review",
  "step_history": [
    "parse_task",
    "plan_search",
    "search_sources"
  ],
  "evidence": [
    "source_1",
    "source_2"
  ],
  "last_tool_result": {
    "tool_name": "send_email",
    "status": "needs_review"
  },
  "proposed_action": {
    "tool_name": "send_email",
    "preview": {
      "recipient": "user@example.com",
      "subject": "Summary of requested task"
    }
  },
  "used_tools": ["search", "send_email"],
  "risk_flags": ["critical_write_action"],
  "human_approvals": []
}
```

### 4.2 Policy Engine
#### Operazioni Interne
Applica regole statiche e deterministiche.
Questo layer deve essere il controllo più forte e prevedibile dell'architettura.

Per il PoC, la decisione non dipende solo dal fatto che il tool sia `read` o `write`, ma anche da:
- dominio del task
- sensibilità dei dati
- irreversibilità dell'azione
- livello di rischio dichiarato dal tool
- presenza o meno delle evidenze minime richieste

#### Decisioni Possibili
- `allow`
- `allow_read_only`
- `ask_clarification`
- `require_human_review`
- `deny`

#### Esempi di Regole
- se il tool è `write`, il rischio è `critical` e l'azione è irreversibile -> `require_human_review`
- se le evidenze minime richieste non sono presenti -> `ask_clarification` oppure `deny`
- se il dominio è sensibile ma l'azione proposta è solo `read` -> `allow_read_only`
- se i dati coinvolti sono sensibili -> `require_human_review`

> [!NOTE]
> Simili alle policy di **LangChain**, ma qui pensate in forma semplice da PoC.

#### Esempio di Output
```json
{
  "policy_decision": "allow_read_only",
  "requires_human_review": false,
  "policy_flags": ["sensitive_domain"],
  "policy_reason": "task allowed, but only read actions are permitted"
}
```

#### Altro Esempio di Output
```json
{
  "policy_decision": "require_human_review",
  "requires_human_review": true,
  "policy_flags": ["critical_write_action", "irreversible_action"],
  "policy_reason": "write action with external side effects in sensitive context"
}
```

### 4.3 Confidence, Risk & Trajectory Engine
#### Operazioni Interne
Per il PoC conviene separare nettamente qualità delle evidenze, rischio operativo e salute della traiettoria, invece di comprimere tutto in un unico `confidence_score`.

Questo riduce la falsa precisione numerica e rende il modello più semplice da spiegare e difendere.

#### Score Distinti
- `evidence_quality_score`: qualità, quantità e coerenza delle evidenze raccolte
- `operational_risk_score`: rischio operativo dell'azione proposta, dato tool, side effects, irreversibilità e sensibilità dei dati
- `trajectory_health_score`: stato della traiettoria corrente, letto come progresso del task e coerenza generale del percorso

#### Segnali Possibili
- per `evidence_quality_score`: numero di evidenze rilevanti, source agreement, copertura minima del task
- per `operational_risk_score`: tipo di tool, side effects, reversibilità, data sensitivity
- per `trajectory_health_score`: consumo del budget, numero di replanning, errori recenti, progresso rispetto al goal

#### Soglie PoC
- `evidence_quality >= 0.75`, `operational_risk <= 0.30` e `trajectory_health >= 0.70` -> `continue`
- `evidence_quality < 0.50` -> `request_more_evidence`
- `trajectory_health < 0.45` -> `replan`
- `operational_risk > 0.75` -> `wait_human_review`

#### Esempio di Output
```json
{
  "evidence_quality_score": 0.78,
  "operational_risk_score": 0.22,
  "trajectory_health_score": 0.74,
  "decision_hint": "continue"
}
```

#### Altro Esempio di Output
```json
{
  "evidence_quality_score": 0.42,
  "operational_risk_score": 0.64,
  "trajectory_health_score": 0.58,
  "decision_hint": "request_more_evidence"
}
```

### 4.4 Judge
#### Operazioni Interne
Il `Judge` viene usato come livello secondario e non come guardiano principale.

Per il PoC, controlli come loop, ripetizione dei tool e sforamento del budget dovrebbero essere gestiti in modo deterministico dal runtime o dalle policy.

Il `Judge`, probabilmente un piccolo **LLM-as-a-judge**, si concentra invece su casi più semantici:
- drift rispetto al goal iniziale
- incoerenza tra evidenze e azione proposta
- risposta intermedia non ben allineata al task
- motivazione dell'azione troppo debole o incompleta

#### Esempio di Output
```json
{
  "judge_verdict": "warning",
  "trajectory_ok": false,
  "issues": ["weak_alignment_with_goal"],
  "recommended_action": "ask_user_clarification"
}
```

### 4.5 Decisione Finale del Layer 4
#### Logica di Combinazione
Prima parlano le policy, poi i tre score del layer 4.3, poi il judge nei casi ambigui o semantici.

Per il PoC è più chiaro descrivere la decisione finale come transizione operativa tra stati.

#### Tabella di Transizione PoC
| Condizione principale | Decisione finale | Stato successivo |
| --- | --- | --- |
| `policy_decision = deny` | `stop` | `aborted` |
| `policy_decision = ask_clarification` | `ask_user_clarification` | `waiting_human_review` |
| `policy_decision = require_human_review` | `wait_human_review` | `waiting_human_review` |
| `policy_decision = allow_read_only` | `continue_read_only` | `acting` |
| `evidence_quality_score < 0.50` | `request_more_evidence` | `observing` |
| `trajectory_health_score < 0.45` | `replan` | `replanning` |
| `operational_risk_score > 0.75` | `wait_human_review` | `waiting_human_review` |
| `judge_verdict = warning` | `ask_user_clarification` | `waiting_human_review` |
| altrimenti | `continue` | `acting` |

### 4.6 Output Finale del Layer 4
Output unificato, spiegabile e tracciabile dell'oversight core.

#### Esempio
```json
{
  "audit_id": "ovr_001",
  "policy": {
    "policy_decision": "allow",
    "requires_human_review": false,
    "policy_flags": ["min_evidence_not_met"]
  },
  "scores": {
    "evidence_quality_score": 0.42,
    "operational_risk_score": 0.64,
    "trajectory_health_score": 0.58,
    "decision_hint": "request_more_evidence"
  },
  "judge": {
    "judge_verdict": "warning",
    "trajectory_ok": false,
    "issues": ["weak_alignment_with_goal"],
    "recommended_action": "ask_user_clarification"
  },
  "triggered_rules": ["min_evidence_not_met", "semantic_alignment_warning"],
  "evidence_ids_used": ["source_1", "source_2"],
  "rationale": "Le evidenze raccolte non sono ancora sufficienti e l'allineamento semantico con il goal risulta ancora debole.",
  "next_allowed_actions": [
    "request_more_evidence",
    "ask_user_clarification",
    "replan"
  ],
  "state_transition": {
    "from": "observing",
    "to": "acting"
  },
  "final_decision": "request_more_evidence"
}
```

### 4.7 Framework / Librerie
Policy logic e score functions custom in Python, Pydantic per strutturare input/output, eventuale LLM economico solo per il judge semantico o per casi ambigui, eventuali evaluator esterni solo se realmente necessari nel PoC.

## 5. HITL GATEWAY

### 5.1 Input
Decisione di human review proveniente dall'oversight core, insieme a task JSON, stato corrente del task, azione proposta dall'agente, evidenze raccolte e motivazione della review.

#### Esempio
```json
{
  "task_id": "task_001",
  "current_step": "send_email",
  "proposed_action": {
    "tool_name": "send_email",
    "tool_input": {
      "recipient": "client@example.com",
      "subject": "Follow-up",
      "body": "..."
    }
  },
  "oversight_reason": "critical_write_action + insufficient_evidence",
  "evidences": [
    "source_1",
    "source_2"
  ]
}
```

### 5.2 Operazioni Interne
Costruisce il pacchetto da mostrare al supervisore umano e sospende temporaneamente il workflow dell'agente.
Il pacchetto deve contenere tutto ciò che serve per prendere una decisione senza dover rileggere l'intera run.

#### Contenuto Minimo del Pacchetto
- contesto del task
- step corrente
- azione proposta
- evidenze raccolte
- motivo della review
- eventuali policy flag
- eventuali score di `evidence_quality`, `operational_risk` e `trajectory_health`

#### Interfaccia Umana per il PoC
- input manuale da terminale
- mini web form
- file / JSON di approvazione simulato

### 5.3 Output
Decisione umana strutturata, che viene rimandata al runtime.

#### Esempio di Output: Approve
```json
{
  "human_decision": "approve",
  "notes": "azione approvata",
  "edited_action": null
}
```

#### Esempio di Output: Edit
```json
{
  "human_decision": "edit",
  "notes": "modificare il destinatario",
  "edited_action": {
    "tool_name": "send_email",
    "tool_input": {
      "recipient": "manager@example.com",
      "subject": "Follow-up",
      "body": "..."
    }
  }
}
```

#### Esempio di Output: Request More Evidence
```json
{
  "human_decision": "request_more_evidence",
  "notes": "serve una seconda fonte indipendente",
  "edited_action": null
}
```

### 5.4 Azioni Possibili
`approve` / `reject` / `edit` / `request_more_evidence` / `stop`

### 5.5 Logica PoC
- se `approve` -> il runtime riprende ed esegue l'azione
- se `reject` -> l'azione non viene eseguita
- se `edit` -> il runtime riprende con l'azione modificata
- se `request_more_evidence` -> il runtime torna indietro e cerca nuove evidenze
- se `stop` -> il task viene chiuso

### 5.6 Framework / Librerie
Per il PoC basta Python semplice, terminal input oppure mini interfaccia web leggera.
Se il runtime è LangGraph, questo blocco può essere collegato a pause/resume e human approval flow.

## 6. MEMORY & EVIDENCE STORE

### 6.1 Input
Task JSON, stato aggiornato del runtime, risultati dei tool, decisioni dell'oversight core, decisioni umane dell'HITL Gateway.

#### Esempio
```json
{
  "task_id": "task_001",
  "task_state": {
    "current_step": "search_sources",
    "completed_steps": 2
  },
  "tool_result": {
    "tool_name": "search",
    "status": "success",
    "payload": {
      "results": ["source_1", "source_2"]
    }
  },
  "oversight_decision": {
    "final_decision": "continue"
  },
  "human_decision": null
}
```

### 6.2 Operazioni Interne
Salva in modo strutturato tutto ciò che serve per ricostruire l'esecuzione del task.
Per il PoC sono previste quattro aree semplici.

#### Aree Principali
- **working state** -> stato corrente del task
- **episodic memory** -> cronologia degli step eseguiti
- **evidence store** -> fonti, risultati dei tool, evidenze raccolte
- **feedback store** -> eventuali interventi umani, annotazioni e correzioni

Conserva in modo ordinato tutto ciò che conta per:
- far ripartire il workflow
- giustificare una decisione
- mostrare contesto all'umano
- supportare evaluation e replay

Per il PoC si può usare una struttura semplice tipo JSON o SQLite.

### 6.3 Output
Memoria aggiornata del task, evidenze aggiornate, storico decisioni.

#### Esempio: Working State
```json
{
  "task_id": "task_001",
  "status": "in_progress",
  "current_step": "evaluate_sources",
  "completed_steps": 2,
  "pending_action": null
}
```

#### Esempio: Evidence Store
```json
{
  "task_id": "task_001",
  "evidences": [
    {
      "source_id": "source_1",
      "source_type": "web",
      "content": "official statement ...",
      "used_in_step": "search_sources"
    },
    {
      "source_id": "source_2",
      "source_type": "web",
      "content": "press release ...",
      "used_in_step": "search_sources"
    }
  ]
}
```

#### Esempio: Feedback Store
```json
{
  "task_id": "task_001",
  "human_actions": [
    {
      "step": "send_email",
      "decision": "edit",
      "notes": "change recipient"
    }
  ]
}
```

### 6.4 Logica PoC
Ogni volta che succede qualcosa di importante, il layer salva:
- stato del task
- step eseguito
- risultato del tool
- decisione di oversight
- eventuale decisione umana

In questo modo il sistema può sempre sapere:
- dove si trova nel workflow
- quali evidenze ha raccolto
- cosa è già stato deciso
- se c'è stato intervento umano

### 6.5 Framework / Librerie
Per il PoC bastano JSON locali, file system oppure SQLite.
Se serve più struttura, si possono usare Pydantic model per serializzare working state, evidenze e feedback.

## 7. OBSERVABILITY & REPLAY

### 7.1 Input
Eventi provenienti da runtime, tool bus, oversight core, HITL Gateway e memory layer.

### 7.2 Operazioni Interne
Raccoglie e invia gli eventi principali del sistema a un framework esterno di observability, in modo da avere tracing, monitoring e dashboard già pronti.

Per il PoC questo layer deve servire a:
- tracciare la run completa
- monitorare metriche operative semplici
- visualizzare tool call, escalation e interventi umani
- permettere replay e debug della sequenza di eventi

### 7.3 Eventi Minimi da Tracciare
- inizio task
- step del runtime
- tool call
- tool result
- decisioni dell'oversight
- escalation HITL
- decisione umana
- completamento o fallimento del task

### 7.4 Output
Trace strutturate, metriche operative essenziali, dati per replay e debug.

### 7.5 Framework / Librerie
Framework esterno di observability / tracing scelto per il PoC.

## 8. EVALUATION LAB

### 8.1 Input
Trace della run, outcome finale del task, decisioni dell'oversight, decisioni umane dell'HITL Gateway, evidenze raccolte.

#### Esempio
```json
{
  "task_id": "task_001",
  "task_status": "completed",
  "trace_id": "trace_001",
  "oversight_decisions": [
    {"final_decision": "continue"},
    {"final_decision": "wait_human_review"}
  ],
  "human_decisions": [
    {"human_decision": "approve"}
  ]
}
```

### 8.2 Operazioni Interne
Valuta come si è comportato il sistema durante la run.
Per il PoC questo layer deve misurare almeno:
- task success
- qualità della traiettoria
- rispetto delle policy
- numero di escalation
- numero di interventi umani
- qualità / sufficienza delle evidenze
- comportamento in condizioni di incertezza

Per il PoC basta una valutazione semplice, basata su metriche e regole chiare, senza costruire un framework troppo pesante.

### 8.3 Output
Report di evaluation strutturato + metriche della run.

#### Esempio
```json
{
  "task_id": "task_001",
  "evaluation": {
    "task_success": true,
    "trajectory_quality": 0.72,
    "policy_compliance": true,
    "escalation_count": 1,
    "human_intervention_count": 1,
    "evidence_sufficiency": 0.80,
    "uncertainty_handling": 0.65
  }
}
```

### 8.4 Logica PoC
Questo layer non modifica il sistema.
Legge i dati della run e produce una fotografia sintetica di come è andata, utile sia per analisi ex post sia per il layer di miglioramento successivo.

### 8.5 Framework / Librerie
Per questo layer si possono integrare framework esterni come:
- OpenAI Evals
- DeepEval
- Confident AI
- Galileo
- Maxim AI
- LangSmith
- Langfuse

Per il PoC basta anche una valutazione custom semplice in Python, purché le metriche siano chiare.

## 9. FEEDBACK & CONTINUOUS IMPROVEMENT LOOP

### 9.1 Input
Report di evaluation, annotazioni umane, errori ricorrenti, override, casi ambigui, falsi positivi / negativi di escalation.

#### Esempio
```json
{
  "task_id": "task_001",
  "evaluation": {
    "task_success": true,
    "trajectory_quality": 0.72,
    "uncertainty_handling": 0.40
  },
  "human_feedback": [
    {
      "decision": "edit",
      "notes": "serviva una seconda fonte"
    }
  ]
}
```

### 9.2 Operazioni Interne
Usa i risultati dell'evaluation e il feedback raccolto per capire cosa migliorare nel sistema.
Per il PoC questo layer può intervenire su:
- soglie di `evidence_quality`, `operational_risk` e `trajectory_health`
- policy di escalation / human review
- prompt
- routing dei tool
- dataset di test
- casi difficili da riutilizzare nelle evaluation future

L'idea è che questo layer non misura, ma trasforma i risultati delle misure in aggiornamenti del sistema.

### 9.3 Output
Configurazione aggiornata del sistema oppure suggerimenti di miglioramento.

#### Esempio
```json
{
  "updated_config": {
    "confidence_threshold": 0.70,
    "escalation_policy": {
      "require_second_source_for_sensitive_tasks": true
    }
  },
  "notes": [
    "alzata la soglia di confidenza",
    "aggiunta regola per richiedere una seconda fonte"
  ]
}
```

### 9.4 Logica PoC
Per il PoC non serve un adattamento automatico completo.
Basta dimostrare che:
- il feedback umano viene salvato
- i risultati dell'evaluation vengono letti
- alcune soglie / regole / configurazioni possono essere aggiornate in modo semplice

Questa parte può essere anche semi-manuale.

### 9.5 Framework / Librerie
Per il PoC bastano Python semplice, file JSON, SQLite oppure configurazioni aggiornabili a mano.
Se serve, questo layer può riusare output di LangSmith, Langfuse, Galileo, Maxim AI o altri tool di evaluation.
