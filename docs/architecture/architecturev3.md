# ARCHITECTURE V3

**Convenzioni Terminologiche**
- `macro-block`: raggruppamento architetturale di primo livello
- `layer`: responsabilità logica interna al macro-block
- `node`: unità eseguibile del grafo LangGraph
- `node + external interface`: nodo del grafo che sospende o riprende il flusso, ma richiede una UI o un input esterno
- `external component`: componente esterno al grafo, invocato dal runtime o eseguito fuori dal ciclo live
- `hybrid component`: componente che combina memoria nativa del grafo e persistenza o logica custom esterna

---

## Focus della tesi e confine del contributo

Questa architettura è il proof of concept di un **motore auditabile di Human
Oversight e Human-in-the-Loop per agenti AI autonomi**. L'agente plan-and-execute è
una parte essenziale della tesi: costituisce il sistema realmente operativo che viene
supervisionato e rende osservabili pianificazione, azione, uso dei tool, incertezza,
progresso e possibili effetti esterni. Non è però presentato come unico contributo
scientifico.

Il contributo architetturale centrale è il control plane che:

- osserva l'esecuzione senza sostituirsi all'actor;
- separa integrità delle evidenze, salute della traiettoria e adeguatezza semantica;
- lascia a OPA la sola direttiva finale;
- decide ai confini governati quando l'autonomia può continuare e quando occorre
  correggere, ripianificare, chiedere chiarimenti, coinvolgere un supervisore o fermare;
- rende ogni gate umano reason-scoped e ogni autorizzazione legata all'esatto digest;
- conserva stato, decisioni, evidenze, interventi e receipt in forme tipizzate,
  riproducibili e utilizzabili per audit ed evaluation.

La tesi valuta quindi l'oversight su un agente autenticamente autonomo. L'autonomia è
il necessario substrato sperimentale; Human Oversight, HITL, auditabilità e qualità
dell'escalation costituiscono il contributo distintivo da dimostrare. Il termine
"innovativo" descrive l'ipotesi progettuale e deve essere sostenuto dai risultati
sperimentali, non assunto come conclusione a priori.

---

## Macro Architecture

### Macro-Blocks

L'architettura viene compressa in cinque macro-blocchi:

1. `Task Intake`
   Punto di ingresso controllato del sistema. Trasforma una richiesta libera in un payload strutturato e validato.

2. `Runtime & Governance`
   Cuore live del sistema supervisionato e del motore di oversight. Separa esecuzione autonoma, produzione dei segnali, decisione OPA, escalation e human review.

3. `Tool Bus / External Capabilities`
   Strato di esecuzione delle capacità esterne. Valida, controlla e invoca i tool.

4. `State, Evidence & Observability`
   Substrato di accountability. Mantiene stato, evidenze, decisioni, interventi umani, eventi e materiali utili ad audit, replay e valutazione.

5. `Evaluation & Continuous Improvement`
   Strato post-run. Misura come si è comportato il sistema e produce aggiornamenti di prompt, policy, soglie e configurazioni.

### Ownership dei layer

I quattro layer numerati descrivono responsabilità e ownership, non quattro passaggi sequenziali di un singolo prompt:

- **Layer 1 — Task Intake**: riceve un input esterno e produce il contratto canonico `IntakeResult`; resta fuori dal grafo LangGraph.
- **Layer 2 — Runtime & Governance**: esegue il grafo live, coordina planner/actor, raccoglie i segnali applicabili, applica la direttiva OPA e instrada tool, replanning, HITL ed external execution.
- **Layer 3 — Tool Bus / External Capabilities**: espone il gateway tipizzato, valida e invoca capability; non decide la policy finale.
- **Layer 4 — State, Evidence & Observability**: mantiene stato, evidenze, oversight, checkpoint, trace e artifact; non è un secondo motore decisionale.

`Evaluation & Continuous Improvement` è il Layer 5 offline: la prima slice di Evaluation è implementata, mentre il feedback loop di Continuous Improvement resta differito.

### Flusso End-to-End

- la richiesta entra nel `Task Intake`
- il task viene convertito in `structured_task`
- il `Runtime & Governance` inizializza il `TaskState` e avvia il ciclo operativo
- il `Runtime Core` adotta un pattern **plan-and-execute**: il `planner` costruisce il piano semantico del task e l'`actor` esegue il passo corrente con una logica **ReAct-like** locale
- il runtime propone azioni e, quando necessario, invoca il `Tool Bus`
- il motore di oversight raccoglie i pillar applicabili e OPA restituisce l'unica direttiva
- se la ragione del gate lo richiede, il sistema entra in una `human review` tipizzata e circoscritta
- durante tutta la run, stato, evidenze ed eventi vengono persistiti
- a fine run, i dati vengono letti dallo strato di evaluation e improvement
- il risultato del post-run aggiorna configurazioni, prompt, soglie o regole

### Collocazione Implementativa dei Macro-Blocks

- `Task Intake`: codice esterno al grafo. Prepara il payload iniziale prima dell'avvio del runtime.
- `Runtime & Governance`: implementato principalmente come nodi e subgraph LangGraph.
- `Governance PDP`: OPA (Open Policy Agent) è il policy decision point esterno; il nodo LangGraph `governance` è il policy enforcement point che costruisce fatti, invoca OPA e applica la direttiva restituita.
- `HITL Gateway`: nodo LangGraph con `interrupt()` più control plane di review standalone servito da `src/main_layer2_dashboard.py`. La dashboard valida e deposita la decisione tipizzata in una inbox durabile; il processo runtime originario, unico owner della run, la consuma e applica `Command(resume=...)` sul checkpoint ufficiale LangGraph (`SqliteSaver` locale, `PostgresSaver` in produzione). La inbox è solo trasporto control-plane e gli artifact runtime sono osservabilità: nessuno dei due è stato ripristinabile.
- `Tool Bus / External Capabilities`: componente esterno al grafo, invocato dal nodo `tool_gateway`, implementato in `src/layer3_tool_bus_external_capabilities/`.
- `State, Evidence & Observability`: componente ibrido che fonde gli artefatti nativi prodotti da LangGraph, come stato runtime, checkpoint, thread history, task metadata e interrupt, con il tracciamento custom dell'architettura, come evidenze semantiche, output di governance, rationale, score e audit trail; è implementato in `src/layer4_state_evidence_observability/`.
- `Evaluation & Continuous Improvement`: Layer 5 offline con ingestion dei trace, SQLite, metriche e reporting; feedback loop non ancora implementato.

---

## 1. Task Intake

### 1.1 Input

Richiesta utente o task generico da svolgere.

### 1.2 Operazioni Interne

Questo macro-block è implementato come **codice esterno al grafo LangGraph**.

Responsabilità principali:
- interpretare il task
- estrarre goal, contesto e vincoli
- rilevare la sensibilità senza imporre una tassonomia di dominio
- derivare criteri di successo
- associare una `execution_policy` configurata dal sistema
- produrre metadata di parsing e tracciabilità

Scelte di progettazione:
- il parsing può essere realizzato con un LLM
- la soglia di confidenza e la modalità read-only non vengono inferite dal task
- la soglia di confidenza e la modalità read-only vengono impostate dalla configurazione del sistema

### 1.3 Output

Output canonico:

```json
{
  "structured_task": {
    "task_spec": {
      "goal": "...",
      "context": "...",
      "constraints": [],
      "sensitivity": "medium",
      "success_criteria": [],
      "execution_intent": "proposal_only"
    },
    "execution_policy": {
      "confidence_threshold": 0.65,
      "read_only_mode": false
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
- produrre fatti e segnali tipizzati senza attribuire all'actor autorità di governance
- applicare la direttiva OPA su continuazione, limitazione, correzione, replan, review o stop
- attivare human review soltanto con una ragione esplicita e un insieme di decisioni ammesse
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
- per `execute_after_governance`, esattamente uno step terminale marcato come produttore della proposta external governata
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
- `observations = []`
- `last_tool_result = null`
- `proposed_action = null`
- `governance_output = null`
- `human_decision = null`
- `final_output_text = null`

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
- uno stesso `current_plan_step` può richiedere più iterazioni locali `actor -> actor` e `actor -> governance -> tool_gateway -> observe_tool_result -> actor`
- il `replanner` viene coinvolto dopo un `NEEDS_REPLAN` esplicito e motivato dell'actor, una recovery tecnica tipizzata o una decisione umana; il completamento normale di un passo non causa replanning

#### Governance Core: OPA-first, operazioni external e pilastri active/shadow

Aggiornamento di refactor: il piano semantico non incorpora livelli di evidenza, precision ceilings o meta-step di compliance e il runtime non lo compatta, comprime o riscrive. Uno step che produce affermazioni fattuali dichiara obbligatoriamente il proprio `evidence_burden`. Inoltre ogni step espone `produces_governed_external_effect_proposal`: per `execute_after_governance` esiste esattamente un marker, sullo step terminale; per `proposal_only` non ne esiste alcuno. Planner e replanner hanno un solo tentativo correttivo tipizzato e il preflight runtime impedisce ai port custom di aggirare entrambi i contratti. L’actor riceve il piano completo come contesto, ma può completare soltanto il passo corrente. I tool adapter producono `ToolObservation` minime (`observation_id`, contenuto concreto, locator opzionale e `source_timestamp` opzionale attribuito alla fonte); per HTML e XHTML il timestamp di freshness deriva soltanto da metadata, elementi semanticamente marcati come pubblicazione o un nodo metadata-like il cui testo completo coincide con `Published <date>`, mai da frasi generiche o label di update/revisione. `Last-Modified` non soddisfa la freshness editoriale per alcun MIME type. Goal, context, vincoli e criteri di successo restano istruzioni, non evidenze. Evidence Integrity & Provenance verifica deterministicamente integrità, lineage, provenienza del manifest, copertura del burden, freshness dichiarata, non-circolarità strutturale e ancoraggio letterale dei token estraibili; il corpus di ancoraggio include anche locator e timestamp attribuito. Gli URL sono confrontati per identità canonica: i parametri di tracking non creano una falsa differenza, mentre path e query significativi non sono mai ammessi per sottostringa. Numerazioni strutturali e frammenti interni a identificatori non diventano claim fattuali; gli importi con virgola o punto decimale restano confrontabili senza cambiare valore. Evidence non valuta entailment semantico. Le richieste di recupero producono feedback con observation ID e istruzioni correttive; OPA applica il ceiling al contatore dell'episodio semantico corrente, mentre il totale whole-run resta auditabile. Al ceiling, HITL può aprire un nuovo episodio, richiedere replan o abortire, ma non bypassare l'onere con un'approvazione generica. Il Governed External Semantic Judge possiede il grounding claim→observation e l’adeguatezza dell’esatto payload. Trajectory Health osserva esclusivamente la dinamica della run: le letture di evidenza non sono candidati external e non alimentano action churn; l’intervento umano delimita un nuovo episodio di replan senza cancellare i contatori whole-run. `actor_self_confidence` è un fatto contestuale di incertezza, non una proprietà della trajectory. OPA ammette il judge soltanto dopo i gate deterministici e resta la sola fonte della direttiva finale. Il Judge riceve tutte le osservazioni canoniche disponibili attraverso `judge_evidence_bundle.v1`, una proiezione JSON deterministica e digest-bound incorporata nella request e persistita negli artefatti della run. La selezione actor resta un marker di reliance, il modello non ricerca autonomamente la run e può citare soltanto gli `evidence_id` elencati nel bundle; content digest e process narration non sono esposti come identificatori citabili. [Fondamento scientifico e limiti](../docs/governance_pillars_research_basis.md).

Configurazione sperimentale corrente: il signal service continua a calcolare integralmente i fatti di freshness, inclusi timestamp mancanti, obsoleti o futuri, ma OPA usa `freshness_enforcement_enabled=false`. La freshness resta quindi osservabile e valutabile offline senza produrre da sola `evidence_gap`, richieste di altra evidenza o HITL. Il flag può essere riattivato senza modificare il contratto delle osservazioni.

Il nodo LangGraph `governance` è il **Policy Enforcement Point (PEP)**. OPA è il solo **Policy Decision Point (PDP)**: possiede policy statica, soglie, pesi, precedenze, cap, invarianti e direttiva finale. Python non combina più direttive con un decision combiner nel percorso live.

Flusso di un turno governato:

1. il PEP costruisce `base_facts` versionati da `TaskState` e `ActorResult`;
2. interroga OPA `data.layer2.governance.required_pillars` per sapere quali segnali deterministici sono obbligatori;
3. normalizza l’eventuale azione in una `ExternalOperationProposal` immutabile e raccoglie Evidence e Trajectory;
4. valida un input preliminare e interroga OPA per la `judge_admission` tipizzata;
5. soltanto se ammesso, invoca il Judge sull’esatto payload e sulla completa base semantica osservata;
6. valida l’input finale, interroga nuovamente OPA e applica la sola direttiva restituita;
7. se OPA, Evidence o Trajectory non sono disponibili applica fail-closed tracciato; un Judge obbligatorio indisponibile o non legato ai digest di operation, snapshot e selection porta a `wait_human_review`, mentre failure dei pillar shadow resta solo osservabile.

I servizi di segnale sono moduli locali tipizzati, non PDP separati: possono produrre componenti diagnostiche per trace, ma non scelgono direttive runtime.

I pillar e la loro autorità sono:

| Pilastro | Modalità | Producer | Autorità nella decisione OPA |
| --- | --- | --- | --- |
| `static_policy` | active | fatti deterministici, execution policy, profili capability | hard deny assorbente soltanto per violazioni specifiche |
| `evidence` | active e event-sensitive | osservazioni concrete, lineage, validità dei riferimenti, copertura del burden, ancoraggio letterale e binding candidate/snapshot/selection | richiesta di ulteriore evidenza per gap strutturali concreti; escalation al limite |
| `trajectory` | active sempre | storia aggregata, replan espliciti, tool outcome, recovery, revisioni e churn osservabile | liveness su tutti gli eventi; convergenza e churn verso HITL ai confini decisionali |
| `judge` | active per execution candidate ammesso | assessment della task, della completa base semantica osservata e dell’esatta proposta external, senza narrazione di processo | OPA mappa `safe`, `needs_revision`, `unsafe`, `uncertain` |
| `risk` | shadow | consequence/impact facts da proposta e profilo versionato | nessuna influenza sulla direttiva |
| `behavioral` | shadow | read model SQLite di similarity, deviation e novelty | nessuna influenza sulla direttiva |

`static_policy` non blocca genericamente `write` o `external`: applica soltanto guardrail deterministici, come `read_only_mode` più effetto mutating, profilo database read-only più statement non-`SELECT`, statement SQL ambiguo/multiplo o destinazione esplicitamente proibita. Una `SELECT` aziendale resta `governed_external`, ma passa lo static e richiede il judge prima di essere candidata all’esecuzione.

OPA valuta prima static policy, Evidence Integrity & Provenance e Trajectory Health, quindi ammette il Judge esclusivamente per un nuovo `STEP_COMPLETED` che espone un `execution_candidate` governed-external non negato, con digest valido e senza blocchi deterministici. Static hard deny, gap strutturali delle evidenze, replan ammissibile, stall o pillar required unavailable evitano la chiamata Judge. Un `PROPOSE_ACTION` non fattuale, un output locale, un draft parziale o una lettura read-only (`web_search`, `fetch_url_text`) non pagano il Judge; un `PROPOSE_ACTION` fattuale può invece attivare Evidence, mentre il Judge resta riservato al candidato external completo. Il Judge riceve task autoritativo e criteri, piano completo non autoritativo, summary degli step conclusi, tutte le osservazioni canoniche, selected references, revision findings della stessa operation, proposta, digest e target; non riceve scratchpad, tool history/arguments, eventi runtime, retry/replan/counter, behavioral, dynamic policy o risk e non emette direttive runtime.

Per una comunicazione external, il passo di sintesi dell'actor espone soltanto l'azione tipizzata con `channel`, `recipient`, `subject` e `body`, mentre i completion generici restano esclusi da quel turno. Il runtime deriva boundary, effect, destination class e digest senza accettarli dal provider. Il runtime preserva il piano semantico del planner senza compattarlo o sostituirlo: planner e actor non devono anticipare il giudizio semantico né trasformarlo in meta-step di compliance.

Le direttive possibili sono `continue`, `continue_read_only`, `request_more_evidence`, `revise_action`, `replan`, `wait_human_review`, `ask_user_clarification` e `stop`. `revise_action` invalida la proposta corrente, restituisce findings audit-safe all’actor e conta nella trajectory; non viene usato né `request_more_evidence` per correggere il tono né `replan` per una semplice revisione del payload.

#### HITL Gateway

Il nodo `human_review` chiama `interrupt()` con un payload tipizzato e JSON-safe. La dashboard valida la decisione umana e la registra in una inbox durabile, idempotente rispetto a `thread_id` e `request_id`; non invoca il grafo. Il processo runtime che possiede la lease del thread reclama la decisione e riprende lo stesso `thread_id` tramite `Command(resume=...)` sul checkpointer ufficiale LangGraph, rendendo visibile la continuazione nello stesso terminale. La lease viene rinnovata anche durante attese lunghe di provider o tool. Se l'owner muore, un terminale sostitutivo può usare `--attach-thread` soltanto quando il checkpoint canonico è ancora fermo sulla stessa richiesta; il comando `claimed` o `failed` viene allora reclamato in base all'esatto `request_id`.

Il proof of concept locale usa `SqliteSaver` e una decision inbox SQLite separata. Un deployment multi-host può selezionare `PostgresSaver`, ma deve anche fornire a `ReviewDecisionInboxPort` un adapter transazionale condiviso: una inbox SQLite locale al singolo host non è un trasporto multi-worker valido. La dashboard non ricostruisce mai il runtime da trace o artifact.

Dopo il submit, la richiesta scompare immediatamente dalla coda attiva e compare per due minuti in una proiezione `review_completed` non interattiva. Questo stato indica soltanto che la decisione è stata accettata dal control plane: non equivale alla conclusione della run né autorizza un secondo submit. Gli stati `pending`, `claimed`, `consumed` e `failed` della inbox rendono auditabile la consegna senza sostituire lo stato canonico di `TaskState`.

Le azioni umane sono canoniche ma reason-scoped: `judge_unsafe`/`judge_uncertain` e le resubmission invariate o già respinte espongono modifica, replan o abort; l'identità di resubmission comprende payload digest ed Evidence-selection digest, quindi una correzione reale dell'attribution non è churn. `capability_approval_required` espone approvazione dell’esatto digest, modifica o abort; clarification e riconciliazione di receipt indeterminate espongono soltanto le scelte applicabili. Nessun altro gate, inclusi checkpoint legacy o reason sconosciute, può autorizzare una nuova esecuzione esterna. `modify_and_continue` richiede un edit tipizzato valido al submit e non può aggirare il contratto della proposta. Approval, completion candidate, Evidence selection e Judge assessment sono legati ai rispettivi digest; la prima proiezione governance di una comunicazione review-ready sigilla atomicamente operation id/digest e selection digest nel candidate ancora privo di binding, mentre una modifica crea un nuovo digest, aggiorna atomicamente il candidate, invalida approval e assessment precedenti e passa da `READY_GOVERNANCE` prima di qualunque seam esecutivo. `approve_continue_read_only` non può autorizzare una proposta mutating invariata.

Il package `src/layer3_tool_bus_external_capabilities/external_operations/` normalizza communication, SQL e generic external. SQL usa AST `sqlglot` e accetta una sola statement; email normalizza campi e destinazione senza giudicare il tono. Il body canonico della comunicazione conserva integralmente il contenuto semantico, mentre soltanto `payload_preview` è bounded: Judge, OPA, HITL ed executor restano così legati allo stesso payload completo e digest. `ExecutionIntent` distingue `proposal_only` da `execute_after_governance` e recupera checkpoint e payload precedenti conservativamente come `proposal_only`.

Il nodo applicativo `external_execution` riceve soltanto una `ExternalExecutionRequest` già autorizzata sul digest esatto. Prima del connector verifica proposta, intent, execution candidate, authorization source, digest, binding del completion candidate a operation/Evidence snapshot/Evidence selection, owner step terminale, capability registry, server/tool allowlist, read-only envelope, idempotency key e ricevute precedenti. Non interpreta testo libero, non modifica payload, non sceglie policy e non espone shell o codice. Una receipt non coerente con operation, capability, digest o idempotency key, una risposta non tipizzata o un'eccezione dopo l'ingresso nel connector vengono normalizzate a `indeterminate` e checkpointate per riconciliazione: l'assenza di risposta non prova l'assenza dell'effetto. Il port è implementato da `MCPExternalOperationExecutor`; il fallback deferred resta fail-closed soltanto quando nessuna capability reale è abilitata.

Il registry external è distinto dal Tool Gateway actor-facing. Le prime capability reali, disabilitate di default, sono `communication.email.send` verso SMTP configurato e `database.statement.execute` verso SQLite selezionato esclusivamente tramite profile id registrato. Server, sessioni, rete, processi, DSN e credenziali restano infrastruttura.

Le ricevute distinguono `executed`, `rejected_before_execution`, `failed` e `indeterminate`. Il retry è limitato a failure deterministiche retryable e usa una nuova attempt scope stabile; `indeterminate` non viene mai ripetuto automaticamente e apre riconciliazione HITL. I ledger persistenti client/server offrono at-most-once per chiave, non exactly-once universale.

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

LangGraph Graph API, OPA HTTP con bundle Rego versionato, Pydantic, SQLite,
SDK Python MCP ufficiale 1.x e LLM opzionale per il judge.

---

## 3. Tool Bus / External Capabilities

### 3.1 Input

Richiesta di esecuzione proveniente dal `Runtime & Governance`.

### 3.2 Operazioni Interne

Questo macro-block è implementato come **componente esterno al grafo**, invocato dal nodo `tool_gateway`, nel package `src/layer3_tool_bus_external_capabilities/`.

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

Le capability mutating non appartengono a questo registry. Dopo governance/HITL,
`external_execution` usa un registry dedicato non visibile all'actor e un pool MCP
composition-owned, thread-safe e riutilizzabile. MCP è un adapter di trasporto: OPA
resta PDP, LangGraph resta PEP e il connettore non può ampliare l'autorità ricevuta.

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

Questo macro-block è un **componente ibrido**, implementato in `src/layer4_state_evidence_observability/`.

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

Il checkpointer LangGraph è l'unica fonte autorevole dello stato necessario a `interrupt / resume`. La decision inbox trasporta soltanto comandi umani digest/request-bound tra control plane e runtime owner; trace JSON, snapshot dashboard e inbox non devono essere usati per ricostruire una run.

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

Questo macro-block è esterno al ciclo live del grafo. La prima slice di Evaluation è implementata in `src/layer5_evaluation_continuous_improvement/`.

Stato attuale: ingestion idempotente di `layer2_trace.v6`, proiezione SQLite, annotazioni separate, metriche aggregate e report JSON/CSV/SVG. Il trace e il `runtime_summary` includono anche la classificazione tipizzata `termination_reason` per distinguere `governance_denied`, `human_abort`, `governance_invariant_failure`, `external_execution_rejected`, `external_execution_failed` e `runtime_failure`. Il feedback loop resta fuori dallo scope corrente.

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
- precisione e appropriatezza delle escalation
- tasso di escalation evitabile e tasso di mancata escalation
- distribuzione delle decisioni umane per ragione del gate
- validità del binding tra decisione umana, candidato e digest
- completezza e replayability dell'audit trail
- qualità e utilizzabilità del feedback umano
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

SQLite e CLI Python standalone; reporting JSON/CSV e SVG senza dipendenze di plotting esterne.

---

# Use case e protocollo di valutazione

## Completion, recovery e approval boundary

## Ordered capability batches

Un actor turn può produrre un batch ordinato di capability request. Il runtime non
analizza dipendenze: esegue una richiesta alla volta, applicando ProgressGuard,
OPA e Tool Gateway a ogni elemento, e richiama l'actor soltanto quando la coda è
vuota. Completion, replan, chiarimento e proposte esterne restano outcome singoli
e non fanno parte di un batch.

Un output dell'actor non è una transizione di stato per il solo fatto di essere
narrativo. Un risultato `STEP_COMPLETED` crea un `StepCompletionCandidate`:
il cursore e la history avanzano soltanto con il commit atomico successivo alla
direttiva OPA. I failure tecnici (protocollo provider, duplicate, timeout e
no-state-delta) restano nel recovery runtime prima della governance semantica e
non diventano evidence né progresso. Il Judge riceve la proposta esterna
immutabile, la completa base semantica osservata in proiezioni bounded e i selected
refs come marker di attribution, senza la narrazione di processo. Il suo assessment
è riusato soltanto se coincidono operation digest, evidence snapshot digest ed
evidence selection digest. Il `approval_mode` del capability
profile (`always`, `conditional`, `never`) è un fatto inviato a OPA: Python
applica la direttiva, senza aggiungere gate per tipo di capability.

I casi sperimentali devono coprire almeno: (1) ricerca read-only con evidenza insufficiente recuperabile, (2) degrado di traiettoria o failure provider che richiede replan, (3) proposta external benigna che richiede judge ma può continuare senza HITL, (4) proposta unsafe/uncertain o judge indisponibile che richiede HITL, e (5) revisione con nuovo digest. Ogni caso deve esportare trace, modalità active/shadow e outcome atteso dei pilastri.
