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

#### Stream Contract: `Agent Core` -> `Governance Core`

Durante l'esecuzione del `current_plan_step`, l'`Agent Core` emette uno stream strutturato di eventi operativi verso il `Governance Core`.

In questo contesto, "stream continuo" non significa accesso continuo al ragionamento interno grezzo del modello, né monitoraggio onnisciente del runtime.
Significa invece una sequenza ad alta frequenza di eventi operativi osservabili emessi dopo ogni transizione rilevante del passo corrente.

Lo stream non è:
- una chain-of-thought grezza
- una sequenza di checkpoint statici business-specific
- un unico esito finale emesso solo a step concluso

Lo stream è invece:
- un canale event-driven di osservabilità operativa
- una sequenza di aggiornamenti compatti, tipizzati e leggibili
- il principale input live del `Governance Core`

Eventi minimi attesi nello stream:
- inizio del `current_plan_step`
- proposta di azione operativa
- richiesta di uso di un tool o capacità esterna
- esito osservato del tool o della micro-azione eseguita
- aggiornamento delle evidenze rilevanti
- segnali di incertezza, rischio o degrado della traiettoria
- produzione di draft, chiarimenti o output intermedi
- completamento del passo oppure richiesta di `replan` / escalation

Proprietà del contract:
- lo stream deve restare compatto, tipizzato e leggibile anche per review umana
- deve descrivere il comportamento osservabile del runtime, non il ragionamento interno grezzo
- il `Governance Core` valuta lo stream in modalità event-driven durante l'operatività del runtime
- l'agente non si ferma per checkpoint hardcoded: l'interruzione avviene solo quando il blocco di governance produce una direttiva di controllo o escalation

Valutazioni attese all'interno del `Governance Core`:
- il layer di `static_policy` legge lo stream e produce una decisione hard come `allow`, `allow_read_only`, `ask_clarification`, `require_human_review` o `deny`
- il layer di `dynamic_policy` usa lo stream per calcolare segnali continui su qualità delle evidenze, rischio operativo e salute della traiettoria
- il layer `behavioral` legge memoria storica e feature delle run precedenti per produrre un assessment storico non bloccante
- il `judge` opera su eventi o snapshot periodici dello stream, senza dipendere da checkpoint statici, e produce una valutazione semantica del comportamento del runtime
- il `decision_combiner` riceve questi esiti e produce la direttiva finale del blocco governance

Ruolo architetturale del contract:
- dare al `Governance Core` visibilità continua sul lavoro dell'agente tramite eventi operativi osservabili
- permettere valutazioni dinamiche di policy, rischio, incertezza e coerenza semantica
- rendere possibile l'attivazione selettiva di `interrupt / resume` e HITL

#### Governance Core

Implementazione corrente: **nodo LangGraph `governance` con componenti interni tipizzati**.

Direzione architetturale futura: **subgraph LangGraph esplicito**, quando HITL `interrupt / resume`, judge semantico e osservabilita avanzata richiederanno nodi separati.

Componenti principali nella baseline corrente:
- `static_policy`, backed by OPA HTTP
- `dynamic_policy`, deterministico e configurato da `configs/layer2_oversight_policy.json`
- `behavioral_foundation`, backed by SQLite locale e feature storiche tipizzate
- `decision_combiner`, tipizzato in `governance.py`

Componenti futuri:
- `judge_node`, come sensore semantico separato
- `hitl_gateway`, come interrupt/resume reale invece di solo stato terminale tipizzato

Ruolo:
- osservare in continuo lo stream operativo emesso dall'`Agent Core`
- valutare policy, rischio, incertezza, contesto storico e coerenza semantica durante l'esecuzione
- trasformare gli esiti dei sottosistemi in una decisione di controllo spiegabile e tracciabile

#### Static Policy

Implementazione: **nodo del grafo + componente esterno OPA (Open Policy Agent)**.

Ruolo:
- applicare il **deterministic policy envelope** del sistema
- valutare vincoli non negoziabili prima dell'esecuzione di azioni operative o richieste di tool
- stabilire ciò che è ammissibile, ciò che è consentito solo in `read_only` e ciò che richiede obbligatoriamente revisione umana

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

Il layer di `static_policy` definisce i confini hard del runtime.
I livelli successivi non possono mai allargare ciò che la `static_policy` ha negato o ristretto.

#### Dynamic Policy

Implementazione corrente: **componente deterministico interno al nodo `governance`**.

File principali:
- `src/layer2_runtime_governance/oversight.py`
- `configs/layer2_oversight_policy.json`

Persistenza runtime:
- `TaskState.last_oversight_assessment`
- `TaskState.oversight_history`
- `TaskState.last_dynamic_policy_assessment`
- `TaskState.dynamic_policy_history`

Score distinti:
- `evidence_quality_score`
- `operational_risk_score`
- `trajectory_health_score`

Ruolo:
- operare come **deterministic adaptive policy layer**
- intervenire quando nessuna policy hard ha bloccato l'azione, ma il runtime non è sufficientemente robusto per proseguire in autonomia
- evitare un unico `confidence_score` aggregato
- separare qualità delle evidenze, rischio operativo e salute della traiettoria
- supportare decisioni come `continue`, `replan`, `request_more_evidence` o escalation

Questo blocco non stabilisce se un'azione è ammissibile in assoluto; valuta se un'azione già ammissibile è sufficientemente supportata, prudente e robusta da eseguire nel contesto corrente.

La baseline implementata evita intenzionalmente un unico `confidence_score`.
Le formule sono configurabili e auditabili:
- `evidence_quality` combina validazione del passo, confidence dell'actor, evidenze osservate, locator di provenienza e successo dell'ultimo tool
- `operational_risk` combina rischio del tool nel registry, sensibilita del task, impatto esterno, capability write-like e richiesta di human review
- `trajectory_health` penalizza troppi turni actor, tool falliti, segnali `needs_replan` e incertezza residua con confidence bassa

Granularita dell'evidenza:
- le affermazioni cronologiche ufficiali come `entered into force`, `applies from`, `applies on` e `timeline` devono normalmente essere trattate come `detailed_extracted`
- `primary_legal` resta riservato a threshold, articoli, numeri di utenti, turnover e altri dettagli giuridici strettamente normativi
- i summary brevi e ad alto livello devono restare eseguibili con evidenza `high_level_summary`: parole generali come gatekeeper, obblighi, divieti, enforcement o core platform services non devono trasformare automaticamente il task in un'estrazione clause-by-clause
- esempi granulari di obblighi, divieti o requisiti specifici devono essere richiesti solo quando il task li domanda o quando sono direttamente presenti nelle evidenze osservate

Il layer produce una raccomandazione, non un permesso assoluto.
OPA resta il confine hard: la `dynamic_policy` puo solo confermare o rendere piu prudente la decisione di policy.

#### Behavioral Foundation

Implementazione corrente: **componente interno al nodo `governance` con persistenza SQLite locale**.

File principali:
- `src/layer2_runtime_governance/behavioral.py`
- `artifacts/runtime/behavioral/behavioral.sqlite3`

Persistenza runtime:
- `TaskState.last_behavioral_assessment`
- `TaskState.behavioral_history`

Ruolo:
- materializzare un assessment storico della run corrente a partire da memoria locale persistita
- salvare snapshot strutturati di run, step, tool call, decisioni di governance, segnali dinamici e feature aggregate
- preparare il terreno per similarity search, deviation analysis e novelty scoring futuri
- rendere disponibile al `decision_combiner` un contesto storico tipizzato senza introdurre ancora nuova autorità decisionale

Responsabilità della baseline attuale:
- leggere la memoria SQLite locale
- esporre un `BehavioralAssessment` tipizzato e leggibile
- persistere feature e snapshot della run corrente come read model storico

Limiti espliciti della baseline attuale:
- il layer `behavioral` non produce ancora `novelty_score` o `behavior_deviation_score`
- il layer `behavioral` non seleziona ancora task simili con ranking forte
- il layer `behavioral` non modifica ancora la direttiva finale del runtime
- la memoria SQLite non sostituisce la trace completa: è un read model indicizzato, non la source of truth primaria

Direzione evolutiva:
- aggiungere similarity e deviation rispetto a run storiche comparabili
- trasformare l'assessment storico in segnale advisory ad alta priorità
- mantenere comunque il vincolo che `behavioral` non possa rendere il sistema più permissivo della `static_policy`

#### Guardrail Proporzionati

Un obiettivo architetturale esplicito e che i vincoli di governance migliorino l'affidabilita senza degradare inutilmente il loop agentico.
I guardrail devono quindi essere:
- proporzionati alla richiesta utente e al livello di precisione atteso
- cumulativi solo quando misurano rischi diversi, non quando duplicano lo stesso blocco
- capaci di ridurre la precisione del draft prima di aumentare l'escalation
- verificabili nel trace, in modo che ogni `request_more_evidence`, `replan` o `wait_human_review` mostri quale vincolo ha inciso

Per task piccoli come "fonti ufficiali + riassunto breve", il runtime deve preferire:
- piano compatto
- una o due raccolte di evidenza ufficiale
- sintesi al livello di precisione supportato
- nessun replan se il problema si risolve abbassando granularita o usando evidenza gia raccolta

Questa scelta non indebolisce la governance: separa il controllo utile dall'attrito operativo. Un claim preciso non supportato resta bloccato, ma un summary alto livello non deve fallire solo perche contiene termini normali del dominio.

#### Judge

Implementazione futura: **nodo del subgraph di governance**.

Ruolo:
- operare come sensore semantico live del runtime
- intercettare problemi di coerenza non catturati dalle sole policy o dagli score quantitativi
- misurare drift rispetto al goal
- rilevare incoerenza tra evidenze raccolte e azione proposta
- rilevare incoerenza tra traiettoria pianificata e comportamento operativo effettivo

Il `judge` non sostituisce né il `Policy Engine` né il motore di confidence/risk.
Non decide da solo i vincoli hard del sistema.
Produce invece una valutazione semantica che può contribuire a `continue`, `replan`, `request_more_evidence`, `wait_human_review` o `stop` attraverso il `decision_combiner`.

#### Decision Combiner

Implementazione corrente: **funzione tipizzata in `governance.py`**.

Ruolo:
- ricevere gli esiti di `static_policy`, `dynamic_policy` e `behavioral`
- produrre una sola direttiva di controllo per il runtime
- applicare una gerarchia esplicita di decisione

Gerarchia di combinazione:
1. la `static_policy` ha priorità sui vincoli non negoziabili
2. la `dynamic_policy` può rendere più prudente la decisione entro i confini ammessi dalla policy
3. il layer `behavioral` nella baseline corrente contribuisce solo come contesto tracciato e non cambia ancora la direttiva finale
4. il `judge` contribuira in futuro come segnale semantico ad alta priorita, ma non potra ampliare cio che la policy ha vietato

Regole correnti:
- `OPA deny` diventa sempre `stop`
- `OPA require_human_review` diventa sempre `wait_human_review`
- `OPA ask_clarification` diventa sempre `ask_user_clarification`
- `OPA allow` con rischio operativo alto diventa `wait_human_review`
- `OPA allow` con completion fragile diventa `request_more_evidence`
- `OPA allow` con traiettoria degradata diventa `replan`
- l'assessment `behavioral` corrente viene tracciato e persistito ma non altera ancora l'output del combiner
- `OPA allow` con segnali sani conserva la direttiva policy-derived

Output possibili:
- `continue`
- `continue_read_only`
- `request_more_evidence`
- `replan`
- `wait_human_review`
- `ask_user_clarification`
- `stop`

#### HITL Gateway

Implementazione: **nodo LangGraph + interfaccia esterna**.

L'interfaccia adottata è `LangGraph Studio / LangSmith Studio`, usata per:
- visualizzare thread e stato corrente del workflow
- ispezionare la history della run
- gestire la pausa del grafo e la successiva ripresa operativa

Il meccanismo HITL rimane comunque basato su `interrupt / resume` del runtime LangGraph.

HITL non è un passaggio obbligatorio tra `planner` e `actor`.
Il runtime opera normalmente in autonomia e l'intervento umano entra solo quando viene attivato da un trigger di oversight significativo.

Il `HITL Gateway` non viene attivato direttamente da un singolo segnale isolato, ma dalla direttiva finale prodotta dal `decision_combiner`.

In particolare:
- una decisione di policy `require_human_review` può attivare direttamente HITL
- segnali di confidence/risk o del `judge` possono portare a HITL solo quando il `decision_combiner` li valuta come non risolvibili con semplice `replan`, `request_more_evidence` o chiarimento

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

Checkpointer LangGraph, eventuale store LangGraph, SQLite, JSON locali, Pydantic.

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

Responsabilità principali:
- valutare come si è comportato il sistema
- misurare qualità della traiettoria e gestione dell'incertezza
- verificare il rispetto delle policy
- usare memoria storica e feature aggregate per identificare pattern ripetuti, casi anomali e deviazioni
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

Da definire.

---

# USE CASE

Si definiscono tre tasks diverse che serviranno per testare e verificare la buona riuscita dell'architettura.

TODO!
