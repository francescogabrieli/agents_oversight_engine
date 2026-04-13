# ARCHITECTURE V3

**Convenzioni Terminologiche**
- `macro-block`: raggruppamento architetturale di primo livello
- `layer`: responsabilità logica interna al macro-block
- `node`: unità eseguibile del grafo LangGraph
- `node + external interface`: nodo del grafo che sospende o riprende il flusso, ma richiede una UI o un input esterno
- `external component`: componente esterno al grafo, invocato dal runtime o eseguito fuori dal ciclo live
- `hybrid component`: componente che combina memoria nativa del grafo e persistenza o logica custom esterna

---

## Macro Architecture

### Macro-Blocks

L'architettura viene compressa in cinque macro-blocchi:

1. `Task Intake`
   Punto di ingresso controllato del sistema. Trasforma una richiesta libera in un payload strutturato e validato.

2. `Runtime & Governance`
   Cuore live del sistema. Gestisce orchestrazione del task, controllo di policy, valutazione del rischio e human review.

3. `Tool Bus / External Capabilities`
   Strato di esecuzione delle capacità esterne. Valida, controlla e invoca i tool.

4. `State, Evidence & Observability`
   Strato di persistenza operativa. Mantiene stato, evidenze, eventi e materiali utili al replay e alla valutazione.

5. `Evaluation & Continuous Improvement`
   Strato post-run. Misura come si è comportato il sistema e produce aggiornamenti di prompt, policy, soglie e configurazioni.

### Flusso End-to-End

- la richiesta entra nel `Task Intake`
- il task viene convertito in `structured_task`
- il `Runtime & Governance` inizializza il `TaskState` e avvia il ciclo operativo
- il runtime propone azioni e, quando necessario, invoca il `Tool Bus`
- il blocco di governance valuta policy, rischio, qualità delle evidenze e possibile escalation
- se necessario, il sistema entra in `human review`
- durante tutta la run, stato, evidenze ed eventi vengono persistiti
- a fine run, i dati vengono letti dallo strato di evaluation e improvement
- il risultato del post-run aggiorna configurazioni, prompt, soglie o regole

### Collocazione Implementativa dei Macro-Blocks

- `Task Intake`: codice esterno al grafo. Prepara il payload iniziale prima dell'avvio del runtime.
- `Runtime & Governance`: implementato principalmente come nodi e subgraph LangGraph.
- `Policy Engine`: nodo del grafo che invoca un componente esterno OPA.
- `HITL Gateway`: nodo LangGraph con `interrupt()` più interfaccia di review. Nel PoC viene usato `LangGraph Studio / LangSmith Studio` per visualizzare thread, stato e history del workflow e per gestire operativamente le pause del grafo.
- `Tool Bus / External Capabilities`: componente esterno al grafo, invocato dal nodo `tool_gateway`.
- `State, Evidence & Observability`: componente ibrido tra memoria nativa LangGraph e persistenza custom esterna.
- `Evaluation & Continuous Improvement`: codice esterno, eseguito fuori dal ciclo live del task.

---

## 1. Task Intake

### 1.1 Input

Richiesta utente o task generico da svolgere.

### 1.2 Operazioni Interne

Questo macro-block è implementato come **codice esterno al grafo LangGraph**.

Responsabilità principali:
- interpretare il task
- estrarre goal, contesto e vincoli
- classificare dominio e sensibilità
- derivare criteri di successo
- associare una `execution_policy` configurata dal sistema
- produrre metadata di parsing e tracciabilità

Scelte di progettazione:
- il parsing può essere realizzato con un LLM leggero
- soglia di confidenza e budget non vengono inferiti dal task
- soglia di confidenza e budget vengono impostati dalla configurazione del sistema

### 1.3 Output

Output canonico:

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
      "success_criteria": []
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

### 1.4 Framework / Librerie

Prompt + LLM API, parser Python, Pydantic.

---

## 2. Runtime & Governance

### 2.1 Input

Input strutturato proveniente dal `Task Intake`.

### 2.2 Operazioni Interne

Questo macro-block è implementato principalmente come **nodi e subgraph LangGraph**.

Responsabilità principali:
- inizializzare il `TaskState`
- pianificare il passo successivo
- proporre azioni operative
- decidere se eseguire, limitare, ripianificare o fermare
- attivare human review quando necessario
- chiudere il task con stato `completed` o `aborted`

Componenti interni principali:

#### Runtime Core

Implementazione: **nodi del grafo LangGraph**.

Nodi principali:
- `bootstrap_from_intake`
- `planner`
- `actor`
- `tool_gateway`
- `observe_tool_result`
- `replanner`
- `finalize`
- `abort`

Stati minimi del task:
- `planning`
- `acting`
- `observing`
- `replanning`
- `waiting_human_review`
- `completed`
- `aborted`

Informazioni minime del `TaskState`:
- `intake_payload`
- stato corrente
- step corrente
- step history
- evidenze raccolte
- ultimo tool result
- azione proposta
- motivazione sintetica
- output di governance
- eventuale decisione umana

#### Governance Core

Implementazione: **subgraph LangGraph**.

Componenti principali:
- `policy_engine_node`
- `confidence_risk_trajectory_node`
- `judge_node`
- `decision_combiner_node`

Ruolo:
- trasformare una proposta operativa in una decisione di controllo spiegabile e tracciabile

#### Policy Engine

Implementazione: **nodo del grafo + componente esterno OPA (Open Policy Agent)**.

Decisioni possibili:
- `allow`
- `allow_read_only`
- `ask_clarification`
- `require_human_review`
- `deny`

Segnali usati dalle policy:
- tipo di tool
- dominio del task
- sensibilità dei dati
- irreversibilità dell'azione
- rischio del tool
- evidenze minime disponibili
- vincoli organizzativi
- eventuali precedenti approvazioni umane

#### Confidence, Risk & Trajectory

Implementazione: **nodo del subgraph di governance**.

Score distinti:
- `evidence_quality_score`
- `operational_risk_score`
- `trajectory_health_score`

Ruolo:
- evitare un unico `confidence_score`
- separare qualità delle evidenze, rischio operativo e salute della traiettoria
- supportare decisioni come `continue`, `replan` o `request_more_evidence`

#### Judge

Implementazione: **nodo del subgraph di governance**.

Ruolo:
- intercettare problemi semantici
- misurare drift rispetto al goal
- rilevare incoerenza tra evidenze e azione proposta

#### HITL Gateway

Implementazione: **nodo LangGraph + interfaccia esterna**.

Per il PoC, l'interfaccia adottata è `LangGraph Studio / LangSmith Studio`, usata per:
- visualizzare thread e stato corrente del workflow
- ispezionare la history della run
- gestire la pausa del grafo e la successiva ripresa operativa

Il meccanismo HITL rimane comunque basato su `interrupt / resume` del runtime LangGraph.

Azioni umane possibili:
- `approve`
- `reject`
- `edit`
- `request_more_evidence`
- `stop`

### 2.3 Output

Output principali:
- `continue`
- `continue_read_only`
- `request_more_evidence`
- `replan`
- `wait_human_review`
- `ask_user_clarification`
- `stop`

Output operativi del blocco:
- tool request verso il `Tool Bus`
- aggiornamento di stato
- richiesta di human review
- output finale del task

### 2.4 Framework / Librerie

LangGraph Graph API, OPA, Pydantic, LLM per il judge.

---

## 3. Tool Bus / External Capabilities

### 3.1 Input

Richiesta di esecuzione proveniente dal `Runtime & Governance`.

### 3.2 Operazioni Interne

Questo macro-block è implementato come **componente esterno al grafo**, invocato dal nodo `tool_gateway`.

Responsabilità principali:
- verificare che il tool esista
- leggere i metadati del registry
- validare l'input
- distinguere tra tool `read` e `write`
- supportare `dry_run` o `preview`
- eseguire il tool oppure produrre una proposta leggibile
- restituire un risultato strutturato

Metadati minimi utili:
- `side_effects`
- `reversible`
- `idempotent`
- `input_schema`
- `output_schema`
- `retry_policy`
- `data_sensitivity`
- `supports_dry_run`

### 3.3 Output

Output strutturato del tool:

```json
{
  "status": "success",
  "tool_name": "search",
  "action_type": "read",
  "payload": {
    "results": ["..."]
  },
  "requires_review": false
}
```

### 3.4 Framework / Librerie

Wrapper Python, registry custom, Pydantic, SDK dei servizi esterni.

---

## 4. State, Evidence & Observability

### 4.1 Input

Input provenienti da:
- runtime
- governance
- tool bus
- human review

### 4.2 Operazioni Interne

Questo macro-block è un **componente ibrido**.

È costruito dalla fusione di:
- **memory nativa di LangGraph**, usata per working state, checkpoint, pause/resume e stato del grafo
- **layer custom esterno**, usato per salvare evidenze, risultati dei tool, decisioni di governance, interventi umani ed eventi di osservabilità

Responsabilità principali:
- persistere il `TaskState`
- salvare le evidenze raccolte
- salvare risultati dei tool
- salvare decisioni di governance
- salvare decisioni umane
- raccogliere eventi per tracing e replay

Aree logiche principali:

#### LangGraph Runtime Memory

Responsabilità:
- working state del task
- checkpoint della run
- supporto a pause e resume
- stato minimo necessario al workflow live

#### Custom Evidence Layer

Responsabilità:
- evidence store
- risultati dei tool
- riferimenti alle fonti
- motivazioni e artefatti utili al controllo

#### Oversight & HITL History

Responsabilità:
- decisioni policy
- score di rischio e traiettoria
- escalation
- interventi umani

#### Observability Events

Eventi minimi:
- inizio task
- step del runtime
- tool request
- tool result
- decisione di governance
- escalation HITL
- decisione umana
- completamento o fallimento

### 4.3 Output

Output disponibili:
- stato persistito
- evidenze persistite
- trace per replay e debug
- materiali per evaluation

### 4.4 Framework / Librerie

Checkpointer LangGraph, eventuale store LangGraph, SQLite, JSON locali, Pydantic per serializzazione.

---

## 5. Evaluation & Continuous Improvement

### 5.1 Input

Input principali:
- outcome del task
- trace della run
- decisioni policy
- decisioni umane
- evidenze raccolte
- feedback umano

### 5.2 Operazioni Interne

Questo macro-block è implementato come **codice esterno al ciclo live del grafo**.

Responsabilità principali:
- valutare come si è comportato il sistema
- misurare qualità della traiettoria e gestione dell'incertezza
- verificare il rispetto delle policy
- trasformare i risultati della evaluation in aggiornamenti del sistema

Metriche minime consigliate:
- task success
- qualità della traiettoria
- compliance alle policy
- numero di escalation
- numero di interventi umani
- sufficienza delle evidenze
- comportamento in condizioni di incertezza

Aree di miglioramento possibili:
- prompt
- soglie
- policy
- dataset di test
- casi difficili da riutilizzare

### 5.3 Output

Output possibili:
- report di evaluation
- soglie aggiornate
- regole aggiornate
- prompt o configurazioni aggiornate

### 5.4 Framework / Librerie

OpenAI Evals, DeepEval, LangSmith, Langfuse oppure valutazione custom in Python.




# USE CASE 

Si definiscono tre tasks diverse che serviranno per testare e verificare la buona riuscita dell'architettura.
