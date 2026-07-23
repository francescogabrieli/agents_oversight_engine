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
- il `Runtime Core` adotta un pattern **plan-and-execute**: il `planner` costruisce il piano semantico del task e l'`actor` esegue il passo corrente con una logica **ReAct-like** locale
- il runtime propone azioni e, quando necessario, invoca il `Tool Bus`
- il blocco di governance valuta policy, rischio, qualità delle evidenze, coerenza semantica e possibile escalation
- se necessario, il sistema entra in `human review`
- durante tutta la run, stato, evidenze ed eventi vengono persistiti
- a fine run, i dati vengono letti dallo strato di evaluation e improvement
- il risultato del post-run aggiorna configurazioni, prompt, soglie o regole

### Collocazione Implementativa dei Macro-Blocks

- `Task Intake`: codice esterno al grafo. Prepara il payload iniziale prima dell'avvio del runtime.
- `Runtime & Governance`: implementato principalmente come nodi e subgraph LangGraph.
- `Governance PDP`: OPA (Open Policy Agent) è il policy decision point esterno; il nodo LangGraph `governance` è il policy enforcement point che costruisce fatti, invoca OPA e applica la direttiva restituita.
- `HITL Gateway`: nodo LangGraph con `interrupt()` più control plane di review standalone servito da `src/main_layer2_dashboard.py`, che riprende esclusivamente checkpoint ufficiali LangGraph con `Command(resume=...)` (`SqliteSaver` locale, `PostgresSaver` in produzione); gli artifact runtime sono osservabilità, non stato ripristinabile.
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

Il `Runtime Core` adotta un pattern **plan-and-execute**.
Il nodo `planner` costruisce e aggiorna il piano semantico del task, mentre il nodo `actor` esegue esclusivamente il passo corrente del piano.
L'esecuzione del singolo passo avviene con una logica **ReAct-like** locale: l'`actor` ragiona sul passo corrente, propone un'azione operativa, osserva gli esiti disponibili e decide se il passo può proseguire, concludersi o richiedere `replan`, chiarimento o escalation.
Questa logica ReAct non coincide con l'intera architettura, ma solo con il comportamento operativo locale dell'`actor` all'interno del pattern plan-and-execute.

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
- `governance`
- `tool_gateway`
- `observe_tool_result`
- `replanner`
- `human_review`
- `finalize_bootstrap`

Ruolo dei nodi principali:
- `bootstrap_from_intake`: inizializza il `TaskState` a partire dall'`IntakeResult`
- `planner`: componente deliberativa che produce il piano semantico completo del task a partire dallo stato corrente
- `actor`: componente esecutiva principale dell'agente, che trasforma il passo pianificato corrente in un'azione concreta o in una proposta operativa
- `governance`: policy enforcement point che chiede a OPA la direttiva finale per il turno corrente
- `tool_gateway`: punto di accesso controllato verso il `Tool Bus` e le capacità esterne
- `observe_tool_result`: osserva gli effetti dell'azione eseguita e li converte in evidenze
- `replanner`: aggiorna il piano quando servono correzioni, nuove evidenze o adattamento della traiettoria
- `human_review`: sospende con `interrupt()` e applica la decisione tipizzata ricevuta da `Command(resume=...)`
- `finalize_bootstrap`: chiude la slice in stato `completed`; una direttiva `stop` porta invece a `aborted`

#### Ruolo di `planner` e `actor`

`planner` e `actor` appartengono alla stessa entità agentica del Layer 2, ma restano separati per mantenere il sistema spiegabile, governabile e adatto alla trajectory analysis.

L'architettura adotta un pattern **plan-and-execute**:
- `planner` decide **quale traiettoria semantica seguire**
- `actor` decide **come realizzare operativamente il passo corrente della traiettoria**

Responsabilità principali di `planner`:
- leggere goal, contesto, vincoli, stato corrente ed evidenze raccolte
- produrre o aggiornare il piano semantico del task
- identificare il `current_plan_step`
- esprimere il razionale sintetico della traiettoria proposta
- non emettere direttamente una `tool_call` esecutiva completa

Output atteso di `planner`:
- `plan_steps` ordinati e orientati all'obiettivo
- identificazione del passo corrente
- eventuali indicazioni di `read_only`, raccolta evidenze, chiarimento o sintesi finale
- nessun payload operativo completo già pronto per l'esecuzione

`actor` è la componente esecutiva del runtime.
L'`actor` non pianifica l'intera traiettoria, ma realizza il `current_plan_step` con una logica **ReAct-like locale**.

Responsabilità principali di `actor`:
- prendere in input il `current_plan_step` e il contesto globale del piano
- svolgere il reasoning operativo necessario per quel passo
- proporre una singola azione operativa concreta oppure un output intermedio o finale
- richiedere l'uso di tool quando necessario
- usare gli esiti osservati per decidere se il passo corrente può dirsi completato oppure se servono ulteriori micro-azioni, chiarimento, `replan` o escalation

Questo confine è intenzionale:
- `planner` definisce la `intended trajectory`
- `actor` realizza la `executed trajectory`
- `observe_tool_result` registra la `observed trajectory`

Questa separazione rende leggibile l'analisi della traiettoria, perché permette di confrontare piano, azione effettiva ed esito osservato.

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

#### Intra-Node Contract: `planner` -> `actor`

Il nodo `actor` riceve in input il `TaskState` aggiornato dal `planner`.

Questo significa che l'input dell'`actor` non è un singolo step isolato, ma l'intero piano semantico corrente insieme allo stato runtime rilevante.

Campi minimi richiesti in ingresso:
- `task_spec`
- `execution_policy`
- `current_state = planning` oppure `acting`
- `plan_steps`
- `current_plan_step`
- `current_plan_step_index`
- `step_history`
- `collected_evidence`
- `last_tool_result`
- `runtime_flags`
- eventuale `governance_output` precedente rilevante

Vincoli del contract:
- `actor` riceve l'intero `plan_steps` come **contesto globale**
- `actor` ha autorità operativa solo sul `current_plan_step`
- `actor` non può riscrivere il piano
- `actor` non può cambiare l'ordine dei `plan_steps`
- `actor` non può aggiungere o rimuovere step
- `actor` non può avanzare autonomamente il `current_plan_step_index`
- se il piano non è più adeguato, `actor` non lo modifica: produce invece un esito che porta a `replanner` o a governance

Ruolo del contract:
- permettere all'`actor` di usare il piano completo come contesto
- limitare il potere decisionale dell'`actor` al passo operativo corrente
- preservare il `planner` come unica fonte autorizzata della `intended trajectory`
- mantenere leggibile la distinzione tra piano previsto, azione eseguita ed esito osservato

Output atteso di `actor` verso il resto del runtime:
- una proposta operativa concreta coerente con il `current_plan_step`
- oppure una richiesta di chiarimento
- oppure un esito di blocco
- oppure un output intermedio o finale

Vincoli sull'output di `actor`:
- l'output di `actor` può contenere dettagli operativi concreti
- l'output di `actor` non equivale a una decisione di policy
- l'output di `actor` non sostituisce il piano del `planner`
- ogni deviazione di traiettoria deve restare esplicita e passare da `replanner`, governance o HITL

Nota operativa:
- il completamento di un `current_plan_step` non coincide necessariamente con una singola azione
- uno stesso `current_plan_step` può richiedere più iterazioni locali `actor -> tool_gateway -> observe_tool_result`
- il `replanner` viene coinvolto quando il passo è completato, quando le evidenze raccolte invalidano il piano corrente, oppure quando la governance richiede correzione, chiarimento o escalation

#### Governance Core: OPA-first, operazioni external e pilastri active/shadow

Il nodo LangGraph `governance` è il **Policy Enforcement Point (PEP)**. OPA è il solo **Policy Decision Point (PDP)**: possiede policy statica, soglie, pesi, precedenze, cap, invarianti e direttiva finale. Python non combina più direttive con un decision combiner nel percorso live.

Flusso di un turno governato:

1. il PEP costruisce `base_facts` versionati da `TaskState` e `ActorResult`;
2. interroga OPA `data.layer2.governance.required_pillars` per sapere quali segnali sono obbligatori;
3. normalizza l’eventuale azione in una `ExternalOperationProposal` immutabile e invoca separatamente i servizi dei pilastri;
4. valida `GovernanceDecisionInput` e lo invia a OPA `data.layer2.governance.decision`;
5. valida `GovernanceDecisionOutput`, registra `GovernanceDecisionTrace` e applica la direttiva al grafo;
6. se OPA, evidence o trajectory non sono disponibili applica fail-closed tracciato; un judge obbligatorio indisponibile o con digest non coincidente porta a `wait_human_review`, mentre failure dei pillar shadow resta solo osservabile.

I servizi di segnale sono moduli locali tipizzati, non PDP separati: possono produrre componenti diagnostiche per trace, ma non scelgono direttive runtime.

I pillar e la loro autorità sono:

| Pilastro | Modalità | Producer | Autorità nella decisione OPA |
| --- | --- | --- | --- |
| `static_policy` | active | fatti deterministici, execution policy, profili capability | hard deny assorbente soltanto per violazioni specifiche |
| `evidence` | active | evidenza osservata, validation e precisione del claim | gap e mismatch di precisione |
| `trajectory` | active | contatori per-step, errori provider/tool, replan, recovery e revisioni | recovery, replan, HITL o stop |
| `judge` | active quando richiesto | assessment semantico tipizzato dell’esatta proposta external | OPA mappa `safe`, `needs_revision`, `unsafe`, `uncertain` |
| `risk` | shadow | consequence/impact facts da proposta e profilo versionato | nessuna influenza sulla direttiva |
| `behavioral` | shadow | read model SQLite di similarity, deviation e novelty | nessuna influenza sulla direttiva |

`static_policy` non blocca genericamente `write` o `external`: applica soltanto guardrail deterministici, come `read_only_mode` più effetto mutating, profilo database read-only più statement non-`SELECT`, statement SQL ambiguo/multiplo o destinazione esplicitamente proibita. Una `SELECT` aziendale resta `governed_external`, ma passa lo static e richiede il judge prima di essere candidata all’esecuzione.

La prima passata OPA richiede il judge esclusivamente quando la proposta è `execution_candidate`, il boundary è `governed_external` e lo static non ha già prodotto hard deny. Output locali, draft parziali e letture di evidenza pubblica (`web_search`, `fetch_url_text`) non pagano una chiamata judge. Il judge riceve goal, vincoli, step identificato da `actor_result.plan_step_id`, proposta, digest, target ed evidenza rilevante; non riceve behavioral, dynamic policy o risk e non emette direttive runtime.

Le direttive possibili sono `continue`, `continue_read_only`, `request_more_evidence`, `revise_action`, `replan`, `wait_human_review`, `ask_user_clarification` e `stop`. `revise_action` invalida la proposta corrente, restituisce findings audit-safe all’actor e conta nella trajectory; non viene usato né `request_more_evidence` per correggere il tono né `replan` per una semplice revisione del payload.

#### HITL Gateway

Il nodo `human_review` chiama `interrupt()` con un payload tipizzato e JSON-safe. La dashboard riceve la decisione umana e riprende lo stesso `thread_id` tramite `Command(resume=...)` sul checkpointer ufficiale LangGraph. Il backend locale è `SqliteSaver`; un deployment multi-worker può selezionare `PostgresSaver`. La dashboard non ricostruisce mai il runtime da trace o artifact.

Azioni umane canoniche, sempre disponibili in ogni HITL: `approve_continue`, `approve_continue_read_only`, `modify_and_continue`, `request_more_evidence`, `replan`, `ask_clarification` e `abort`. `modify_and_continue` richiede comunque un edit tipizzato valido al submit e non può aggirare il contratto della proposta. Approval e judge assessment sono legati al digest esatto; una modifica crea un nuovo digest, invalida approval e assessment precedenti e passa da `READY_GOVERNANCE` prima di qualunque seam esecutivo. `approve_continue_read_only` non può autorizzare una proposta mutating invariata.

Il package `external_operations/` normalizza communication, SQL e generic external. SQL usa AST `sqlglot` e accetta una sola statement; email normalizza campi e destinazione senza giudicare il tono. I backend reali email/database e ogni side effect external restano differiti: il seam production restituisce un risultato `executed=false`.

Gli artifact per thread (`full-trace-2.json`, snapshot dashboard e payload pending) sono materiale di osservabilità. In locale il checkpoint autoritativo è `artifacts/runtime/checkpoints/layer2_checkpoints.sqlite3`; in produzione può risiedere nel backend PostgreSQL configurato. I checkpoint terminali hanno retention limitata, mentre interrupt pendenti e thread non terminali vengono preservati.

### 2.3 Output

Output principali:
- `continue`
- `continue_read_only`
- `revise_action`
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

LangGraph Graph API, OPA HTTP con bundle Rego versionato, Pydantic, SQLite e LLM opzionale per il judge.

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

Nel PoC corrente il `Tool Gateway` usa MCP come backend operativo principale per il paniere locale di test, ma MCP non viene esposto direttamente all'`actor`.
L'`actor` emette sempre una `ToolRequest` tipizzata; il gateway risolve la richiesta verso un backend configurato, applica allowlist e vincoli read-only, esegue il tool e normalizza l'esito in `ToolExecutionResult`.

Backend supportati nella baseline:
- `mcp`: server locali configurati esplicitamente, senza discovery libera
- `openai`: `web_search`
- `http_fetch`: `fetch_url_text` con protezioni su schema e host
- `local_runtime`: riservato per tool locali futuri

Il paniere MCP locale attuale include tool read-only per:
- echo / connectivity check
- lettura controllata di fixture
- statistiche testuali
- calcolo aritmetico sicuro

Autenticazione e autorizzazione per utente, team o organizzazione non sono ancora implementate.
Il punto di inserimento previsto è sopra il backend MCP, dentro il `Tool Gateway` o in un futuro control layer collegato al gateway.
Anche prima dell'auth completa, il gateway non opera come client MCP aperto: usa solo server configurati, tool allowlisted e parametri costruiti dal contratto tipizzato del runtime.

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
- **layer custom esterno**, usato per salvare evidenze, risultati dei tool, decisioni di governance, interventi umani, eventi di osservabilità e memoria behavioural indicizzata

Responsabilità principali:
- persistere il `TaskState`
- salvare le evidenze raccolte
- salvare risultati dei tool
- salvare decisioni di governance
- salvare assessment statici, dinamici e behavioural
- salvare decisioni umane
- raccogliere eventi per tracing e replay

Aree logiche principali:

#### LangGraph Runtime Memory

Responsabilità:
- working state del task
- checkpoint ufficiale LangGraph della run (`SqliteSaver` locale, `PostgresSaver` in produzione)
- supporto a pause e resume
- stato minimo necessario al workflow live

Il checkpointer LangGraph è l'unica fonte autorevole per `interrupt / resume`. Le trace JSON e gli snapshot dashboard non devono essere usati per ricostruire o continuare una run.

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
- assessment behavioural e feature storiche aggregate
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

`SqliteSaver` / `PostgresSaver` LangGraph, SQLite behavioural read model, JSON di trace/artifact, Pydantic.

---

## 5. Evaluation & Continuous Improvement

### 5.1 Input

Input principali:
- outcome del task
- trace della run
- decisioni policy
- assessment behavioural e feature storiche
- decisioni umane
- evidenze raccolte
- feedback umano

### 5.2 Operazioni Interne

Questo macro-block è implementato come **codice esterno al ciclo live del grafo**.

Responsabilità principali già implementate:
- valutare come si è comportato il sistema
- normalizzare trace esportate senza importare `TaskState` o modelli del runtime live
- invocare OPA `data.layer2.evaluation.compliance`
- produrre report JSON e Markdown su compliance e completezza della trace

Direzione di continuous improvement non ancora automatizzata:
- misurare qualità della traiettoria e gestione dell'incertezza su dataset e run aggregate
- usare memoria storica e feature aggregate per identificare pattern ripetuti, casi anomali e deviazioni
- trasformare risultati approvati in aggiornamenti versionati di prompt, soglie, policy e dataset

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
- segnali behavioural, similarity e novelty
- dataset di test
- casi difficili da riutilizzare

### 5.3 Output

Output possibili:
- report di evaluation
- soglie aggiornate
- regole aggiornate
- prompt o configurazioni aggiornate

### 5.4 Framework / Librerie

CLI Python standalone, normalizzatore di trace, client OPA HTTP e report JSON/Markdown.

---

# Use case e protocollo di valutazione

I casi sperimentali devono coprire almeno: (1) ricerca read-only con evidenza insufficiente recuperabile, (2) degrado di traiettoria o failure provider che richiede replan, (3) proposta external benigna che richiede judge ma può continuare senza HITL, (4) proposta unsafe/uncertain o judge indisponibile che richiede HITL, e (5) revisione con nuovo digest. Ogni caso deve esportare trace, modalità active/shadow e outcome atteso dei pilastri.
