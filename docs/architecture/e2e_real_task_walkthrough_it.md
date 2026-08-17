# Architettura End-to-End su una Task Reale

## Scopo del documento

Questo walkthrough mostra come una richiesta reale attraversa l'implementazione
attuale: intake canonico, bootstrap, piano semantico, actor, Tool Gateway,
governance OPA-first, Human-in-the-Loop e persistenza.

Gli esempi di payload sono illustrativi. Planner e actor possono produrre testi
diversi tra una run e l'altra; i contratti tipizzati, i confini di autorità e le
invarianti descritte qui restano invece deterministici.

## Caso di studio

### Task operativa

```text
Draft an external email to the Acme Cloud operations team about an ongoing
sensitive EU runtime incident affecting the customer notification pipeline,
requesting specific manual checks and a status update within 60 minutes.
```

### Context hint

```text
Incident detected 09:12 CET on May 25, 2026; intermittent delivery failures for
transactional notifications; still under investigation; no confirmed data loss;
delayed processing observed for some EU tenants. Prefer official EU sources.
```

La richiesta dice `Draft`, non chiede di inviare il messaggio. Layer 1 deve quindi
registrare `execution_intent=proposal_only`, salvo che l'utente chieda
esplicitamente l'esecuzione dopo governance. L'intento descrive la richiesta ma non
concede mai autorità operativa.

## Mappa del grafo reale

Il wiring vive in `src/layer2_runtime_governance/graph.py`:

```text
START -> bootstrap_from_intake -> planner -> actor -> governance

governance -> actor
           -> tool_gateway -> observe_tool_result -> actor | governance | replanner
           -> replanner -> actor
           -> human_review
           -> external_execution
           -> finalize_bootstrap -> END

human_review -> planner | actor | tool_gateway | replanner | governance
             | external_execution | finalize_bootstrap

external_execution -> external_execution | human_review | finalize_bootstrap
```

Il self-edge di `external_execution` è soltanto il retry deterministico e limitato
di una ricevuta `failed + retryable`. Una ricevuta `indeterminate` richiede invece
riconciliazione umana.

## Dove vive ogni responsabilità

### Layer 1 — Task Intake

- `src/main_layer1.py`: entrypoint lineare di Layer 1
- `src/layer1_intake/models.py`: `TaskSpec`, `ExecutionPolicy`, `IntakeMetadata`,
  `IntakeResult`
- `src/layer1_intake/service.py`: normalizzazione, parsing, completeness assessment
  e validazione finale

### Layer 2 — Runtime e Governance

- `src/main_layer2.py`: proprietario dell'esecuzione e del resume
- `src/layer2_runtime_governance/graph.py`: wiring LangGraph
- `src/layer2_runtime_governance/runtime/node_adapters.py`: adapter sottili dei nodi
- `src/layer2_runtime_governance/runtime/lifecycle_service.py`: bootstrap e
  finalizzazione
- `src/layer2_runtime_governance/domain/task_state.py`: stato canonico della run
- `src/layer2_runtime_governance/planning/`: planner e replanner
- `src/layer2_runtime_governance/execution/factory.py`: selezione dell'actor
- `src/layer2_runtime_governance/execution/providers/bedrock.py`: adapter actor
  Bedrock
- `src/layer2_runtime_governance/execution/providers/bedrock_runtime.py`: client
  Converse e impostazioni Bedrock
- `src/layer2_runtime_governance/runtime/governance_service.py`: applicazione della
  decisione OPA e routing
- `src/layer2_runtime_governance/governance_components/`: input OPA, pipeline,
  parsing e comportamento fail-closed
- `src/layer2_runtime_governance/hitl/__init__.py`: richieste e decisioni HITL
- `src/layer2_runtime_governance/hitl/dashboard.py`: proiezione per la dashboard

### Layer 3 — Tool Bus e operazioni esterne

- `src/layer3_tool_bus_external_capabilities/tool_contract.py`: `ToolRequest`,
  `ToolExecutionResult`, `ToolObservation` e porta del gateway
- `src/layer3_tool_bus_external_capabilities/tools/gateway.py`: esecuzione delle
  capability read-only registrate
- `src/layer3_tool_bus_external_capabilities/external_operations/`: proposta
  immutabile, normalizzazione, registry, idempotenza ed executor esterno

### Layer 4 — Stato, evidenza e osservabilità

- `src/layer4_state_evidence_observability/oversight/`: contratti delle valutazioni
  persistite
- `src/layer4_state_evidence_observability/observability/`: trace tipizzata e
  summary
- `src/layer4_state_evidence_observability/infrastructure/checkpointing.py`:
  composizione del checkpointer
- `src/layer4_state_evidence_observability/infrastructure/runtime_artifacts.py`:
  artifact per thread
- `src/layer4_state_evidence_observability/infrastructure/review_decision_inbox.py`:
  trasporto durevole delle decisioni dashboard → runtime owner

I file root `state.py`, `governance.py` e `replanning.py` esistono soltanto come
anchor compatibili con checkpoint o import storici. Il codice nuovo deve usare i
moduli capability indicati sopra.

## Passo 1 — Layer 1 produce il contratto canonico

`run_layer2_flow()` in `src/main_layer2.py` consegna testo e context hint a Layer 1.
La shape semantica corrente di `TaskSpec` contiene soltanto:

```json
{
  "goal": "Draft an external email ...",
  "context": "Incident detected 09:12 CET ...",
  "constraints": [],
  "sensitivity": "high",
  "success_criteria": [],
  "execution_intent": "proposal_only"
}
```

Il parser può estrarre vincoli o criteri più specifici; l'esempio non pretende di
riprodurre una risposta LLM esatta. Sono invece invarianti:

- non esistono campi `domain_type` o `domain_label` nel `TaskSpec` attivo;
- Pydantic possiede shape e normalizzazione;
- `assess_task_completeness` produce una sola valutazione semantica tipizzata;
- budget, soglia di confidenza e read-only mode vengono dalla configurazione di
  `ExecutionPolicy`, non dal modello;
- il context mancante può generare un warning, ma non forza da solo lo stato
  `partial`.

Il risultato è un `IntakeResult` con `StructuredTask` e `IntakeMetadata`.

## Passo 2 — Bootstrap costruisce `TaskState`

Il nodo sottile chiama `bootstrap_from_intake()` in
`runtime/lifecycle_service.py`, che delega la trasformazione a
`TaskState.from_intake()` in `domain/task_state.py`.

Il bootstrap:

- conserva l'intero `IntakeResult` in `intake_payload`;
- imposta lo stato iniziale da `ParseStatus`;
- copia `execution_intent` nel contratto runtime;
- aggiunge flag espliciti per intake incompleto, chiarimento richiesto, read-only
  e sensibilità alta quando applicabili;
- registra il primo `StepRecord` con `node_name=bootstrap_from_intake`.

### Confine importante: intake non è evidenza

Goal, context, vincoli e criteri di successo restano istruzioni della task. Non
vengono trasformati in `ToolObservation` e il bootstrap non crea evidenze sintetiche.
All'inizio, in assenza di risultati tool, `TaskState.observations` è vuoto.

Questo evita di confondere una dichiarazione dell'utente con un fatto osservato a
un confine di capability. Se una claim richiede verifica esterna, l'actor deve
acquisire un'osservazione concreta oppure qualificare l'output in modo coerente con
l'incertezza residua.

## Passo 3 — Il planner definisce soltanto la traiettoria semantica

Il planner riceve la task canonica e restituisce una `PlannerDecision`. Ogni
`PlanStep` contiene:

- `step_id`
- `objective`
- `rationale`
- `expected_outcome`
- `status`, posseduto dal runtime

Il planner non emette chiamate tool, payload eseguibili, gate di policy o meta-step
HITL. `runtime/planning_service.py` conserva i passi restituiti senza compattarli,
riscriverli o inserirne altri. Se l'actor segnala `NEEDS_REPLAN`, il replanner
sostituisce la traiettoria corrente e pendente e poi restituisce il controllo
all'actor.

## Passo 4 — L'actor esegue un solo step alla volta

`ActorTask` contiene il piano completo per contesto, ma l'autorità di completion è
limitata a `current_plan_step`. Include inoltre osservazioni, scratchpad audit-safe,
validazioni precedenti, ultimo risultato tool, budget residuo e feedback di
governance.

L'actor restituisce un `ActorResult` tipizzato, per esempio:

- `INTERMEDIATE_OUTPUT`
- `PROPOSE_ACTION`
- `STEP_COMPLETED`
- `ASK_CLARIFICATION`
- `BLOCKED`
- `NEEDS_REPLAN`

L'actor non muta direttamente `TaskState` e non sceglie la rotta finale. Il servizio
runtime valida e materializza il risultato; il grafo passa poi dal confine di
governance.

## Passo 5 — Eventuale acquisizione read-only

Il context chiede di preferire fonti ufficiali UE. Se il piano richiede fatti
esterni, l'actor può proporre una `ToolRequest` per `web_search` o
`fetch_url_text`.

Il flusso è:

1. l'actor produce la richiesta tipizzata;
2. la governance valuta il confine read-only;
3. il Tool Gateway valida registry, schema e budget;
4. l'adapter esegue la capability;
5. l'adapter crea ogni `ToolObservation` con `observation_id`, contenuto concreto e
   locator opzionale;
6. `runtime/tool_service.py` valida e registra le osservazioni senza attribuire
   rank, support level o classificazioni semantiche universali;
7. il runtime torna ad actor, governance o replanner in base all'esito.

Intake e orchestration non fabbricano osservazioni: le crea l'adapter che ha
effettivamente osservato il risultato.

## Passo 6 — Completion ed evidence binding

Per completare uno step, l'actor deve produrre una validazione tipizzata e scegliere
esplicitamente gli `evidence_refs` tra gli ID presenti in `TaskState.observations`.
`execution/result_builder.py` rifiuta riferimenti sconosciuti. Il runtime costruisce
poi digest stabili dello snapshot delle osservazioni e della selezione dichiarata.

La self-confidence dell'actor è un segnale contestuale, non una dichiarazione di
sufficienza e non un'autorizzazione. OPA interpreta i fatti attivi richiesti per
l'evento.

## Passo 7 — Governance OPA-first ed event-sensitive

`governance_components/opa_pipeline.py` raccoglie fatti deterministici e invoca OPA.
OPA/Rego è l'unica autorità della direttiva finale; Python valida la risposta e la
applica senza ricostruire un secondo decision combiner.

I requisiti cambiano con il confine:

- un aggiornamento intermedio ritorna all'actor senza trattare la narrazione come
  degrado della traiettoria;
- una richiesta tool read-only richiede la policy statica;
- un replan esplicito attiva i fatti di traiettoria;
- una completion attiva evidenza e traiettoria;
- un candidato `governed_external` non negato richiede anche il semantic judge
  vincolato allo stesso payload digest.

Rischio operativo e behavioral memory restano shadow-only, con
`decision_influence=false`. Sono visibili in trace ma non possono modificare la
direttiva.

## Passo 8 — Il draft diventa una proposta immutabile

Per una comunicazione esterna l'actor usa l'azione dedicata con `channel`,
`recipient`, `subject` e `body`. Il runtime conserva il draft tipizzato in
`ExternalCommunicationState` e Layer 3 lo normalizza in una
`ExternalOperationProposal` immutabile.

Il body canonico non viene troncato silenziosamente. Il limite di schema viene
verificato prima del connector; soltanto la preview destinata alle interfacce è
limitata. Una proposta review-ready possiede almeno:

- operation ID e capability ID;
- target normalizzato;
- effetto operativo;
- payload canonico;
- payload digest;
- profilo di capability.

Un draft parziale non invoca il judge. Il candidato review-ready viene invece
valutato sul contenuto e sul digest esatti.

## Passo 9 — Human-in-the-Loop digest-bound

Quando OPA richiede review, `build_human_review_request()` in
`hitl/__init__.py` crea una richiesta tipizzata e `interrupt()` sospende il grafo sul
checkpointer ufficiale.

La richiesta può includere operation ID, digest, target, summary della proposta,
finding del judge e proposta completa. Le azioni sono reason-scoped: non ogni tipo
di review espone ogni decisione possibile.

Regole essenziali:

- l'approvazione vale soltanto per il digest corrente;
- `modify_and_continue` applica un edit tipizzato, crea una nuova proposta e un
  nuovo digest, invalida judge/approval precedenti e ritorna a governance;
- `request_more_evidence` e `replan` riaprono soltanto le route consentite dal
  motivo di review;
- `abort` porta a uno stop auditabile;
- il vincolo read-only non può essere aggirato approvando una proposta mutante
  invariata.

La dashboard usa la proiezione di `hitl/dashboard.py`. Valida e accoda la decisione,
ma non esegue `Command(resume=...)`: il resume resta responsabilità del processo
`src/main_layer2.py` che possiede la run.

## Passo 10 — Proposal-only oppure esecuzione esterna

Nel caso di studio l'intento è `proposal_only`. Dopo la review, il risultato può
terminare come `approved_draft`; non entra nell'executor esterno.

Se l'utente avesse chiesto esplicitamente `execute_after_governance`, il percorso
potrebbe continuare soltanto con:

- proposta e digest correnti;
- direttiva OPA valida e decisione umana applicabile;
- capability abilitata nel registry esterno dedicato;
- server e tool MCP allowlisted;
- idempotency key stabile;
- rispetto del read-only envelope.

L'executor restituisce una `ExternalExecutionReceipt`. Lo stato `COMPLETED` per un
task eseguibile richiede una receipt `executed`, oppure una receipt `indeterminate`
riconciliata con una decisione umana legata al digest.

## Passo 11 — Checkpoint, artifact e replay

I checkpoint LangGraph sono l'unica memoria capace di riprendere un interrupt. Gli
artifact di Layer 4 sono proiezioni per audit, dashboard ed evaluation; non
ricostruiscono il grafo.

Per ogni thread, `infrastructure/runtime_artifacts.py` mantiene tra gli altri:

- `full-trace-2.json`
- `trace_summary.md`
- `runtime_summary.json`
- `dashboard_snapshot.json`, che include il DTO della review pendente quando presente

Il dashboard backend scrive decisioni request-bound nella inbox durevole. Il runtime
owner le reclama in modo idempotente e applica `Command(resume=...)` al checkpoint
dello stesso thread e della stessa richiesta.

## Sequenza attesa per il caso di studio

Le decisioni testuali del planner e dell'actor non sono hardcoded, ma le invarianti
portano a questa lettura:

1. Layer 1 produce un `IntakeResult` con intento `proposal_only`.
2. Il bootstrap crea `TaskState` senza osservazioni derivate dall'intake.
3. Il planner restituisce passi semantici e il runtime li conserva integralmente.
4. Se servono fonti ufficiali, l'actor passa dal Tool Gateway read-only e riceve
   osservazioni concrete.
5. L'actor propone il draft completo e seleziona gli eventuali riferimenti usati.
6. Layer 3 proietta il draft in una proposta immutabile digest-bound.
7. OPA ammette il judge per il candidato governato e decide la route.
8. La human review opera sul digest esatto.
9. Essendo `proposal_only`, un'approvazione termina come draft approvato senza invio.
10. Trace, summary, dashboard snapshot e decision history rendono la sequenza
    ricostruibile offline.

Il punto architetturale centrale non è la generazione dell'email: è il fatto che
osservazioni, selezione dell'evidenza, proposta, decisione OPA e intervento umano
rimangano tipizzati, digest-bound e replayable lungo tutto il confine operativo.
