# ARCHITECTURE

**Convenzioni Terminologiche**
- `layer`: responsabilità architetturale del sistema
- `node`: unità eseguibile del grafo LangGraph
- `subgraph`: insieme coordinato di nodi LangGraph
- `external service`: componente esterno al grafo, invocato dal runtime

## 1. TASK & CONTEXT INTAKE

### 1.1 Input
Richiesta utente / task generico da svolgere.

### 1.2 Operazioni Interne
LLM leggero oppure parser che trasforma il task generico in un JSON strutturato.

Questo layer rimane **esterno al grafo LangGraph** e rappresenta il punto di ingresso controllato del sistema.
Il suo ruolo è produrre un payload canonico e validato che verrà poi usato per inizializzare il runtime.

Per il PoC conviene separare:
- `task_spec`: descrizione del task
- `execution_policy`: parametri di esecuzione decisi dal sistema / supervisore

Dal task si estraggono goal, contesto, vincoli e alcune informazioni utili al controllo.
Soglia di confidenza e budget non vengono inferiti dal task, ma impostati come configurazione di esecuzione.

#### Esempio
```json
{
  "structured_task": {
    "task_spec": {
      "goal": "...",
      "context": "...",
      "constraints": [],
      "domain_type": "research",
      "domain_label": "generic_research",
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
  },
  "metadata": {
    "parse_status": "complete",
    "warnings": [],
    "missing_information": []
  }
}
```

### 1.3 Output
Output canonico del layer 1: task JSON strutturato, con separazione tra specifica del task e policy di esecuzione, più metadata di tracciabilità e qualità del parsing.

Questo output non coincide ancora con lo stato runtime del sistema.
Verrà convertito nel `TaskState` iniziale da un nodo di bootstrap del Layer 2.

### 1.4 Framework / Librerie
Prompt + LLM API, parser Python, Pydantic.

---

## 2. AGENT RUNTIME / ORCHESTRATOR

Implementazione proposta: **LangGraph Graph API**.

### 2.1 Input
Output strutturato del Layer 1.

Il runtime non riceve direttamente una richiesta utente libera, ma un payload validato prodotto dal Layer 1.
Il primo nodo del grafo trasforma questo payload nel `TaskState` operativo condiviso.

### 2.2 Operazioni Interne
Questo layer rappresenta il **control plane** del task.

È implementato con **LangGraph Graph API**, che permette di modellare il sistema come:
- nodi discreti
- transizioni esplicite
- stato condiviso persistente
- pause/resume
- branching
- subgraph di governance

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
- `intake_payload`
- stato corrente
- step corrente
- history dei passi eseguiti
- evidenze raccolte
- ultimo risultato di un tool
- azione proposta
- motivazione sintetica della scelta corrente
- output del layer di oversight
- eventuale decisione umana
- riferimenti alle evidenze persistite

#### Esempio
```json
{
  "intake_payload": {
    "structured_task": {
      "task_spec": {
        "goal": "find official sources about X"
      },
      "execution_policy": {
        "confidence_threshold": 0.65,
        "budget": {
          "max_steps": 5
        }
      }
    },
    "metadata": {
      "parse_status": "complete"
    }
  },
  "status": "acting",
  "current_step": "search_sources",
  "step_history": [
    "bootstrap_from_intake",
    "plan_search"
  ],
  "proposed_action": {
    "tool_name": "search",
    "tool_input": {
      "query": "official sources about X"
    }
  },
  "evidence_ids": [],
  "last_tool_result": null,
  "reasoning_note": "need external evidence before proceeding",
  "oversight_result": null,
  "human_decision": null
}
```

### 2.3 Nodi Principali del Grafo
Per il PoC, la struttura consigliata del runtime è la seguente:

- `bootstrap_from_intake`
- `planner`
- `actor`
- `tool_gateway`
- `observe_tool_result`
- `oversight_subgraph`
- `human_review`
- `replanner`
- `finalize`
- `abort`

#### Descrizione sintetica dei nodi

##### `bootstrap_from_intake`
Converte l’output del Layer 1 nel `TaskState` iniziale del runtime.

##### `planner`
Produce il prossimo passo di alto livello dell’agente.

##### `actor`
Traduce il piano corrente in una proposta operativa concreta:
- tool call
- richiesta di chiarimento
- output finale
- passaggio a replanning

##### `tool_gateway`
Nodo ponte tra runtime e Layer 3.
Invoca il Tool Bus esterno e riporta nel grafo il risultato strutturato.

##### `observe_tool_result`
Aggiorna stato ed evidenze dopo l’esecuzione del tool.

##### `oversight_subgraph`
Subgraph dedicato alla governance del task.
Valuta policy, rischio, qualità delle evidenze e coerenza semantica.

##### `human_review`
Nodo HITL del grafo.
Interrompe il workflow, espone il payload di review e riprende con la decisione umana.

##### `replanner`
Ricalcola il piano in caso di traiettoria debole, evidenze insufficienti o richiesta esplicita.

##### `finalize`
Chiude il task con output finale e stato `completed`.

##### `abort`
Chiude il task con stato `aborted`.

### 2.4 Output
Next step / tool request / stato aggiornato / output finale.

### 2.5 Framework / Librerie
LangGraph Graph API, Pydantic, eventuale checkpointer LangGraph per persistence e resume.

---

## 3. TOOL BUS

Collocazione architetturale: **servizio esterno orchestrato da LangGraph**.

### 3.1 Input
Richiesta di tool call dal runtime.

Questa richiesta viene generata dal nodo `tool_gateway` del Layer 2.

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
Questo layer non viene modellato come runtime del workflow, ma come **servizio esterno di esecuzione e validazione** invocato dai nodi del grafo.

Il Tool Bus:
- controlla se il tool esiste
- controlla se il tool è consentito
- legge i metadati del registry
- valida l’input
- distingue tra tool `read` e `write`
- supporta dry run / preview
- esegue la chiamata o produce una proposta leggibile
- restituisce un risultato strutturato al grafo

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

### 3.4 Relazione con LangGraph
Il Layer 3 non governa il flusso del task.
È un capability layer esterno orchestrato da nodi del grafo.

La sequenza tipica è:
- `actor` propone un’azione
- `tool_gateway` invia la richiesta al Tool Bus
- il Tool Bus risponde con un risultato strutturato
- il grafo aggiorna lo stato e decide il passo successivo

### 3.5 Framework / Librerie
Wrapper Python, registry custom, Pydantic, SDK/tool integration dei servizi esterni.

---

## 4. OVERSIGHT CORE

Implementazione proposta: **subgraph di governance dentro LangGraph**.

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
    "bootstrap_from_intake",
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

### 4.2 Struttura del Layer 4
Il Layer 4 è implementato come **subgraph di governance** dentro LangGraph.

Il subgraph è composto da quattro nodi principali:
- `policy_engine_node`
- `confidence_risk_trajectory_node`
- `judge_node`
- `decision_combiner_node`

L’obiettivo del subgraph è trasformare una proposta operativa dell’agente in una decisione di governance tracciabile.

---

### 4.3 Policy Engine

Implementazione proposta: motore esterno deterministico basato su **Open Policy Agent (OPA)**, invocato da un nodo del subgraph.

#### Operazioni Interne
Il Policy Engine viene implementato integrando **Open Policy Agent (OPA)** come motore esterno di decisione policy.

Questo layer rappresenta il controllo più forte, deterministico e prevedibile dell'architettura.
Il suo compito è valutare, in modo dichiarativo e ripetibile, se l'azione proposta dall'agente è consentita, limitata, bloccata oppure soggetta a human review.

Per il PoC, il nodo `policy_engine_node` del subgraph di governance:
- costruisce uno snapshot strutturato del task corrente
- include task, stato, azione proposta, metadati del tool ed eventuali evidenze minime
- invia questo snapshot a OPA
- riceve una decisione policy strutturata
- riporta il risultato nel `TaskState` del grafo

L'obiettivo è separare in modo netto:
- **logica di orchestrazione** -> nel grafo LangGraph
- **logica di policy** -> in OPA, tramite regole dichiarative esterne

In questo modo le policy:
- non sono hardcodate nel runtime
- sono versionabili
- sono facilmente modificabili
- sono spiegabili e auditabili

#### Valutazione Policy
Per il PoC, la decisione non dipende solo dal fatto che il tool sia `read` o `write`, ma anche da:
- dominio del task
- sensibilità dei dati
- irreversibilità dell'azione
- livello di rischio dichiarato dal tool
- presenza o meno delle evidenze minime richieste
- eventuali vincoli operativi o organizzativi
- eventuali precedenti approvazioni umane

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
- se un'azione è esplicitamente vietata da policy organizzativa -> `deny`

#### Esempio di Input verso OPA
```json
{
  "task_id": "task_001",
  "task_spec": {
    "goal": "...",
    "domain_type": "research",
    "sensitivity": "medium"
  },
  "task_state": {
    "status": "acting",
    "current_step": "send_email"
  },
  "proposed_action": {
    "tool_name": "send_email",
    "action_type": "write"
  },
  "tool_metadata": {
    "risk": "critical",
    "reversible": false,
    "data_sensitivity": "medium",
    "side_effects": ["external_communication"]
  },
  "evidence": [
    "source_1",
    "source_2"
  ]
}
```

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

#### Altro Esempio di Output
```json
{
  "policy_decision": "deny",
  "requires_human_review": false,
  "policy_flags": ["organizational_policy_block"],
  "policy_reason": "action denied by explicit organizational rule"
}
```

---

### 4.4 Confidence, Risk & Trajectory Engine

Implementazione proposta: motore custom invocato da un nodo del subgraph.

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

---

### 4.5 Judge

Implementazione proposta: nodo semantico del subgraph.

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

---

### 4.6 Decisione Finale del Layer 4

Implementazione proposta: nodo `decision_combiner_node`.

#### Logica di Combinazione
La logica di combinazione segue una precedenza esplicita: prima le policy, poi i tre score del Layer 4.4, infine il judge nei casi ambigui o semantici.

Per il PoC è più chiaro descrivere la decisione finale come transizione operativa tra stati.

#### Decisioni Possibili
- `continue`
- `continue_read_only`
- `request_more_evidence`
- `replan`
- `wait_human_review`
- `ask_user_clarification`
- `stop`

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

### 4.7 Output Finale del Layer 4
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

### 4.8 Framework / Librerie
LangGraph subgraph, **Open Policy Agent (OPA)** per il policy engine, score functions custom in Python, Pydantic, eventuale LLM economico per il judge.

---

## 5. HITL GATEWAY

Implementazione proposta: nodo LangGraph con `interrupt()` e interfaccia esterna di review.

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
  "evidence": [
    "source_1",
    "source_2"
  ]
}
```

### 5.2 Operazioni Interne
Questo layer è composto da:
- un **nodo del grafo** che usa `interrupt()` per sospendere il workflow
- una **interfaccia esterna** che mostra il payload di review al supervisore umano

Il pacchetto di review deve contenere tutto ciò che serve per prendere una decisione senza dover rileggere l'intera run.

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
Decisione umana strutturata, che viene rimandata al runtime tramite resume del grafo.

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
LangGraph `interrupt()` + checkpointer per pause/resume, Python semplice per la UI del PoC oppure mini interfaccia web leggera.

---

## 6. MEMORY & EVIDENCE STORE

Implementazione proposta: layer ibrido tra Graph State LangGraph e Memory Layer custom.

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
Questo layer è ibrido e si basa su due componenti complementari:

#### A. Graph State + Checkpointer / Store LangGraph
Gestisce:
- working state del task
- persistence a livello di thread
- pause/resume
- state history
- short-term memory del runtime
- eventuale long-term memory cross-thread tramite store

#### B. Memory & Evidence Layer custom
Gestisce:
- episodic memory ricca
- evidence store strutturato
- feedback store
- oversight history
- audit payloads
- dati per replay applicativo ed evaluation

Per il PoC sono previste quattro aree semplici.

#### Aree Principali
- **working state** -> stato corrente del task, mantenuto nel graph state
- **episodic memory** -> cronologia degli step eseguiti
- **evidence store** -> fonti, risultati dei tool, evidenze raccolte
- **feedback store** -> eventuali interventi umani, annotazioni e correzioni

Conserva in modo ordinato tutto ciò che conta per:
- far ripartire il workflow
- giustificare una decisione
- mostrare contesto all'umano
- supportare evaluation e replay

Per il PoC si può usare:
- checkpointer LangGraph per la persistenza runtime
- JSON o SQLite per il layer custom di evidence e feedback

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
  "evidence": [
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

Il `TaskState` del grafo contiene solo il minimo operativo necessario al workflow.
Le evidenze ricche, lo storico di oversight, i feedback umani e i payload di audit vengono persistiti nel layer custom esterno.

### 6.5 Framework / Librerie
Checkpointer LangGraph, eventuale store LangGraph, JSON locali, file system oppure SQLite.
Se serve più struttura, si possono usare Pydantic model per serializzare working state, evidenze e feedback.

---

## 7. OBSERVABILITY & REPLAY

Collocazione architetturale: **layer esterno**.

### 7.1 Input
Eventi provenienti da runtime, tool bus, oversight core, HITL Gateway e memory layer.

### 7.2 Operazioni Interne
Raccoglie e invia gli eventi principali del sistema a un framework esterno di observability, in modo da avere tracing, monitoring e dashboard già pronti.

Per il PoC questo layer deve servire a:
- tracciare la run completa
- monitorare metriche operative semplici
- visualizzare tool call, escalation e interventi umani
- permettere replay e debug della sequenza di eventi

Questo layer resta fuori dal grafo principale.
Riceve eventi dal runtime e dai servizi esterni.

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
LangSmith, Langfuse o framework esterno equivalente per tracing e monitoring.

---

## 8. EVALUATION LAB

Collocazione architetturale: **layer esterno / post-run**.

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

Questo layer non modifica il runtime durante l’esecuzione.
Opera su dati persistiti e trace raccolte a posteriori.

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
OpenAI Evals, DeepEval, Confident AI, Galileo, Maxim AI, LangSmith, Langfuse oppure valutazione custom semplice in Python.

---

## 9. FEEDBACK & CONTINUOUS IMPROVEMENT LOOP

Collocazione architetturale: **layer esterno / semi-manuale**.

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
È sufficiente dimostrare che:
- il feedback umano viene salvato
- i risultati dell'evaluation vengono letti
- alcune soglie / regole / configurazioni possono essere aggiornate in modo semplice

Questa parte può essere anche semi-manuale.

### 9.5 Framework / Librerie
Python semplice, file JSON, SQLite oppure configurazioni aggiornabili a mano.
Se serve, questo layer può riusare output di LangSmith, Langfuse, Galileo, Maxim AI o altri tool di evaluation.
