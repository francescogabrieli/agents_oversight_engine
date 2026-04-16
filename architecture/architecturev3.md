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
- `Policy Engine`: nodo del grafo che invoca un componente esterno OPA (Open Policy Agent).
- `HITL Gateway`: nodo LangGraph con `interrupt()` più interfaccia di review. Viene usato `LangGraph Studio / LangSmith Studio` per visualizzare thread, stato e history del workflow e per gestire operativamente le pause del grafo.
- `Tool Bus / External Capabilities`: componente esterno al grafo, invocato dal nodo `tool_gateway`.
- `State, Evidence & Observability`: componente ibrido che fonde gli artefatti nativi prodotti da LangGraph, come stato runtime, checkpoint, thread history, task metadata e interrupt, con il tracciamento custom dell'architettura, come evidenze semantiche, output di governance, rationale, score e audit trail.
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
- il parsing può essere realizzato con un LLM 
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
- pianificare la traiettoria operativa completa del task
- proporre azioni operative
- eseguire il task in autonomia finché resta entro i vincoli di governance
- decidere se eseguire, limitare, ripianificare o fermare
- attivare human review solo quando necessario
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

Ruolo dei nodi principali:
- `bootstrap_from_intake`: inizializza il `TaskState` a partire dall'`IntakeResult`
- `planner`: componente deliberativa che produce il piano semantico completo del task a partire dallo stato corrente
- `actor`: componente esecutiva principale dell'agente, che trasforma il passo pianificato corrente in un'azione concreta o in una proposta operativa
- `tool_gateway`: punto di accesso controllato verso il `Tool Bus` e le capacità esterne
- `observe_tool_result`: osserva gli effetti dell'azione eseguita e li converte in evidenze
- `replanner`: aggiorna il piano quando servono correzioni, nuove evidenze o adattamento della traiettoria
- `finalize`: chiude il task in stato `completed`
- `abort`: chiude il task in stato `aborted`

#### Ruolo di `planner` e `actor`

`planner` e `actor` appartengono alla stessa entità agentica del Layer 2, ma rimangono separati come responsabilità architetturali per mantenere il sistema spiegabile, governabile e adatto alla trajectory analysis.

In termini concettuali:
- `planner` decide **cosa ottenere dopo**
- `actor` decide **come realizzare operativamente ciò che è stato deciso**

Responsabilità principali di `planner`:
- leggere goal, contesto, vincoli, stato corrente ed evidenze raccolte
- produrre il piano semantico completo della traiettoria prevista del task
- identificare il passo corrente del piano
- esprimere il razionale sintetico della traiettoria proposta
- non emettere direttamente la `tool_call` esecutiva concreta

Output atteso di `planner`:
- step semantici orientati all'obiettivo
- eventuale indicazione di `read_only`, raccolta evidenze, chiarimento o sintesi finale
- nessun dettaglio operativo di basso livello come nome tool obbligatorio, parametri completi o payload già eseguibile

Questo confine è intenzionale:
- `planner` definisce la `intended trajectory`
- `actor` definisce la `executed trajectory`
- `observe_tool_result` registra la `observed trajectory`

Questa separazione rende leggibile l'analisi della traiettoria, perché permette di confrontare piano, azione effettiva ed esito osservato.

`actor` è la principale componente esecutiva dell'`Agent Core` del Layer 2.

Responsabilità principali di `actor`:
- prendere in input il passo corrente o l'azione ad alto livello proposta dal `planner`
- trasformare il passo pianificato in una proposta operativa concreta
- eseguire il reasoning operativo necessario per quel passo
- effettuare eventuali `call_llm` necessarie alla produzione dell'output intermedio o finale
- decidere se per realizzare il passo serva un tool, una risposta intermedia, una richiesta di chiarimento o un output finale
- invocare strumenti esterni quando l'azione richiede accesso a tool o servizi
- produrre un risultato operativo che possa poi essere osservato e trasformato in evidenza

Relazione con gli altri nodi:
- `actor` non decide in autonomia se un'azione è permessa: questa responsabilità rimane al blocco di governance
- `actor` non accede direttamente ai tool in modo incontrollato: quando servono strumenti esterni passa attraverso `tool_gateway`
- `actor` non chiude da solo il ciclo: il risultato della sua esecuzione viene passato a `observe_tool_result`, che aggiorna evidenze e stato

In altre parole:
- `planner` è la parte deliberativa dell'agente
- `actor` è la parte esecutiva dell'agente
- `governance` e `HITL` sono il sistema di controllo che supervisiona l'agente

Questo significa che la principale entità agentica del Layer 2 non coincide con un singolo nodo isolato, ma con il loop operativo:

`planner -> actor -> observe_tool_result -> replanner`

All'interno di questo loop:
- `planner` formula la traiettoria semantica completa prevista del task
- `actor` ne realizza il passo corrente in forma operativa
- `observe_tool_result` registra ciò che è successo
- `replanner` adatta la traiettoria se necessario

Il nodo `actor` è quindi il punto in cui l'intenzione del sistema diventa comportamento operativo reale.

Il loop opera agenticamente in autonomia finché resta entro l'envelope consentito dalla governance.
L'intervento umano non è parte del percorso normale di ogni step, ma una escalation selettiva.

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
- `plan_steps`
- `current_plan_step`
- step history
- evidenze raccolte
- ultimo tool result
- azione proposta
- motivazione sintetica del piano o dell'azione
- output di governance
- eventuale decisione umana

#### Bootstrap Contract: Layer 1 -> Layer 2

Il nodo `bootstrap_from_intake` riceve come input l'intero `IntakeResult` prodotto dal `Task Intake`.

Questo payload viene usato per inizializzare il `TaskState` operativo del runtime.

Campi iniziali minimi:
- `intake_payload = IntakeResult`
- `task_spec = intake_payload.structured_task.task_spec`
- `execution_policy = intake_payload.structured_task.execution_policy`
- `intake_metadata = intake_payload.metadata`
- `step_index = 0`
- `step_history = []`
- `collected_evidence = []`
- `last_tool_result = null`
- `proposed_action = null`
- `governance_output = null`
- `human_decision = null`
- `final_output = null`

Regole iniziali di bootstrap:
- `parse_status = complete` -> `current_state = planning`
- `parse_status = partial` -> `current_state = planning` con flag `incomplete_intake`
- `parse_status = needs_clarification` -> `current_state = waiting_human_review` con flag `clarification_required`

Ruolo del bootstrap:
- validare il payload iniziale
- costruire il `TaskState`
- impostare i campi runtime iniziali
- determinare il primo stato del workflow

Il bootstrap non esegue ancora planning avanzato, policy enforcement completo o tool calling. Queste responsabilità appartengono ai nodi successivi del runtime e della governance.

#### Governance Core

Implementazione: **subgraph LangGraph**.

Componenti principali:
- `policy_engine_node`
- `confidence_risk_trajectory_node`
- `judge_node`
- `decision_combiner_node`

Ruolo:
- osservare il motore agentico durante l'esecuzione e trasformare una proposta operativa o un esito rilevante in una decisione di controllo spiegabile e tracciabile

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
- supportare decisioni come `continue`, `replan`, `request_more_evidence` o escalation
- non ogni degrado della traiettoria richiede automaticamente HITL: in molti casi il primo esito corretto è `replan`

#### Judge

Implementazione: **nodo del subgraph di governance**.

Ruolo:
- intercettare problemi semantici
- misurare drift rispetto al goal
- rilevare incoerenza tra evidenze e azione proposta
- rilevare situazioni in cui la traiettoria pianificata non è più coerente con ciò che il runtime sta realmente facendo

#### HITL Gateway

Implementazione: **nodo LangGraph + interfaccia esterna**.

L'interfaccia adottata è `LangGraph Studio / LangSmith Studio`, usata per:
- visualizzare thread e stato corrente del workflow
- ispezionare la history della run
- gestire la pausa del grafo e la successiva ripresa operativa

Il meccanismo HITL rimane comunque basato su `interrupt / resume` del runtime LangGraph.

HITL non è un passaggio obbligatorio tra `planner` e `actor`.
Il runtime opera normalmente in autonomia e l'intervento umano entra solo quando viene attivato da un trigger di oversight significativo.

Trigger tipici di ingresso in HITL:
- decisione `require_human_review` dal `policy_engine`
- rischio operativo troppo alto o traiettoria degradata non risolvibile con semplice `replan`
- incoerenza semantica forte rilevata dal `judge`
- azione write-like o esternamente impattante che richiede revisione prima dell'esecuzione

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

Wrapper Python, Pydantic, SDK dei servizi esterni.

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

Checkpointer LangGraph, eventuale store LangGraph, SQLite, JSON locali, Pydantic.

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

Da definire.




# USE CASE 

Si definiscono tre tasks diverse che serviranno per testare e verificare la buona riuscita dell'architettura.

TODO!
