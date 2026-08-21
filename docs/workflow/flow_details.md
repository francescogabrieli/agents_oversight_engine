# Dettaglio del flusso runtime

Questo documento descrive il percorso live del **Layer 2 — Runtime & Governance** e il modo in cui coordina Layer 1, Layer 3 e Layer 4. Il contratto di riferimento
resta `docs/architecture/architecturev3.md`; qui l'obiettivo è mostrare come i componenti
collaborano durante una run.

La separazione è questa:

- **Layer 1 — Task Intake** prepara il contratto `IntakeResult` canonico prima di avviare il grafo;
- **Layer 2 — Runtime & Governance** esegue il ciclo LangGraph e prende le decisioni di routing/enforcement;
- **Layer 3 — Tool Bus / External Capabilities** esegue le capability attraverso gateway e adapter tipizzati;
- **Layer 4 — State, Evidence & Observability** conserva e proietta stato, evidenze, checkpoint, trace e artifact;
- **Evaluation** legge i trace esportati a posteriori e non partecipa al percorso live.

Il percorso è descritto dalla prospettiva del contributo centrale della tesi: un
motore auditabile di Human Oversight e HITL applicato a un runtime agentico autonomo.
Planner e actor formano il nucleo agentico supervisionato; Layer 3 espone le capability attraverso il Tool Gateway, mentre OPA, HITL, Layer 4,
checkpoint e trace costituiscono il control plane e il substrato di accountability.

## 1. Flusso completo

```text
START
  -> bootstrap_from_intake
  -> planner
  -> actor
  -> { governance | tool_gateway | replanner | human_review | external_execution }

tool_gateway
  -> progress_guard
  -> { cached observation | backend invocation | finite recovery }
  -> observe_tool_result
  -> actor

actor outcomes are operational only:
  -> capability request | step completion | semantic replan | blocking clarification | external proposal

The actor no longer has a narration-only actor -> actor edge. Audit-safe summaries
are recorded separately from the mutable `AgentWorkspace`, whose durable artifacts
are projected into the next actor context. `progress_epoch` advances only for a
concrete observation, changed artifact, completed step, human decision, or changed
semantic plan. The local ProgressGuard uses a capability-owned action key before
 the backend and before OPA; cache, duplicate, and circuit outcomes are not
backend invocations and cannot poison the circuit breaker.

Legacy topology details retained for Judge/HITL/external completion:
  -> {
       actor
       tool_gateway
       replanner
       human_review
       external_execution
       finalize_bootstrap
     }

tool_gateway
  -> observe_tool_result
  -> actor

replanner
  -> actor

human_review
  -> {
       planner
       actor
       tool_gateway
       replanner
       governance
       external_execution
       finalize_bootstrap
     }

external_execution
  -> {
       external_execution
       human_review
       finalize_bootstrap
     }

finalize_bootstrap
  -> END
```

Il planner decide la traiettoria semantica. L'actor sceglie le azioni concrete.
Il Tool Gateway esegue capacità read-only. La governance valuta i confini
semantici e operativi, non ogni aggiornamento narrativo dell'actor.
OPA restituisce l'unica direttiva runtime. Il Judge interviene sulle proposte
esterne quando richiesto da OPA. HITL autorizza l'esatto payload esterno prima
dell'esecuzione quando il contratto lo impone.

## 2. Layer 1: input canonico

Il runtime non interpreta nuovamente il testo grezzo. Riceve un `IntakeResult`:

```json
{
  "structured_task": {
    "task_spec": {
      "goal": "Risultato richiesto dall'utente",
      "context": "Contesto utile",
      "constraints": ["Vincolo esplicito"],
      "sensitivity": "medium",
      "success_criteria": ["Condizione osservabile di successo"],
      "execution_intent": "proposal_only"
    },
    "execution_policy": {
      "policy_profile_name": "default",
      "confidence_threshold": 0.65,
      "read_only_mode": false
    }
  },
  "metadata": {
    "request_id": "...",
    "parse_status": "complete",
    "warnings": [],
    "missing_information": []
  }
}
```

`execution_intent` distingue una proposta locale da un effetto richiesto dopo
governance. Registra l'intento dell'utente, ma non concede autorità di esecuzione.
Il risultato esterno concreto emerge dal piano e dalla proposta tipizzata prodotta
durante l'esecuzione.

## 3. Bootstrap e `TaskState`

`bootstrap_from_intake` crea il `TaskState`, unica fonte di verità live. I gruppi
principali sono:

- handoff Layer 1: task, policy e metadati;
- stato e contatori runtime;
- piano completo e cursore dello step corrente;
- esiti actor, scratchpad audit-safe e validazioni;
- richieste tool, risultati e osservazioni;
- assessment e decisioni di governance;
- richieste e decisioni HITL;
- proposta, richiesta e receipt di esecuzione esterna;
- cronologia necessaria a trace e dashboard.

Il bootstrap non trasforma goal, contesto, vincoli o criteri di successo in prove.
Le osservazioni entrano nello stato soltanto quando una capacità restituisce
contenuto concreto.

## 4. Planner: proprietà semantica del piano

Il planner produce un `PlannerDecision` con una lista ordinata di `PlanStep`:

```json
{
  "plan_steps": [
    {
      "step_id": "step_1",
      "objective": "Obiettivo operativo completo",
      "rationale": "Perché serve",
      "expected_outcome": "Che cosa deve esistere alla fine",
      "status": "pending"
    }
  ],
  "rationale": "Strategia complessiva"
}
```

Regole:

- il prompt è generale e descrive principi di pianificazione applicabili a task
  diverse;
- ogni passo deve essere autonomo, verificabile e sufficientemente specifico;
- il piano non contiene chiamate tool, payload eseguibili o passi di simulazione
  della governance;
- il runtime conserva esattamente la lista prodotta dal planner;
- il runtime non accorcia, fonde, espande o sostituisce il piano;
- la policy viene validata senza riscrivere la semantica.

Il replanner può sostituire soltanto lo step corrente e quelli ancora pendenti.
La cronologia degli step completati resta immutabile.

## 5. Actor: piano completo, commit sullo step corrente

L'actor riceve un `ActorTask` immutabile con:

- task, policy e metadati;
- piano completo;
- step corrente e relativo indice;
- step già eseguiti;
- osservazioni raccolte;
- ultimo risultato tool;
- scratchpad e validazioni precedenti;
- feedback di governance;
- contatori runtime.

Il piano completo permette all'actor di capire dipendenze, destinazione finale e
coerenza globale. L'autorità di commit resta limitata allo step corrente: l'actor
non può segnare altri passi come completati né mutare direttamente lo stato.

Esiti tipizzati:

- `PROPOSE_ACTION`: propone una richiesta tool o un'azione concreta;
- `INTERMEDIATE_OUTPUT`: registra avanzamento senza chiudere lo step;
- `STEP_COMPLETED`: chiude lo step e fornisce l'output utilizzabile;
- `ASK_CLARIFICATION`: richiede un dato mancante;
- `BLOCKED`: dichiara un impedimento concreto;
- `NEEDS_REPLAN`: chiede una nuova traiettoria semantica.

Gli output intermedi possono estendere il lavoro locale su più turni. Un replay
esattamente equivalente sullo stesso stato osservabile, però, non aggiunge memoria
utile: il runtime lo converte in una richiesta di recovery/replan. Inoltre, quando
`execution_intent=execute_after_governance`, un normale `STEP_COMPLETED` terminale
non può chiudere la task senza una proposta esterna tipizzata; lo stato `COMPLETED`
richiede infine una ricevuta con esito `executed`, oppure un esito `indeterminate`
riconciliato esplicitamente dall'operatore sul digest esatto senza nuovo tentativo.

Non esiste un secondo esito speciale per l'ultimo passo. Quando l'ultimo
`STEP_COMPLETED` supera la governance, il suo `output_text` diventa l'output finale
canonico e la run può terminare.

## 6. Tool Gateway e osservazioni

Una richiesta tool è un `ToolRequest` tipizzato con un `request_id` univoco.
L'actor non chiama direttamente backend HTTP, OpenAI o MCP.

Sequenza:

1. l'actor produce `PROPOSE_ACTION`;
2. la governance valuta la richiesta;
3. `tool_gateway` valida registry, campi e limiti;
4. l'adapter esegue la capacità;
5. il gateway restituisce `ToolExecutionResult`;
6. `observe_tool_result` registra le `ToolObservation`;
7. l'actor riparte con le nuove osservazioni.

Contratto minimo:

```json
{
  "request": {
    "request_id": "...",
    "plan_step_id": "step_1",
    "tool_name": "fetch_url_text",
    "reason": "Leggere il contenuto della fonte",
    "url": "https://example.test/source"
  },
  "success": true,
  "summary": "Contenuto recuperato",
  "content": "Testo aggregato opzionale",
  "observations": [
    {
      "observation_id": "step_1:tool:1:observation:1",
      "content": "Contenuto effettivamente restituito",
      "locator": "https://example.test/source"
    }
  ]
}
```

Una osservazione contiene soltanto:

- identificatore stabile;
- contenuto concreto;
- locator opzionale.

L'adapter crea le osservazioni dal risultato reale della propria azione. Il runtime
ne valida struttura, identità e provenienza dalla tool call, poi le registra senza
classificarle semanticamente. Non assegna rank, etichette di qualità o proprietà
dedotte da parole chiave.

`StepValidation.evidence_refs` è una selezione opzionale di identificatori. Se
l'actor seleziona riferimenti, tutti devono esistere nello stato. L'assenza di
riferimenti non è automaticamente un errore: una query o un effetto possono non
dipendere da fatti raccolti in precedenza. Quando il `PlanStep` dichiara
`produces_factual_claims=true` e un `evidence_burden`, però, una selezione vuota o
insufficiente può produrre un deficit di copertura e una richiesta di ulteriore
evidenza. Per le proposte esterne, il Judge valuta semanticamente contenuto,
contesto, osservazioni e digest.

## 7. Governance OPA-first

Il routing dipende dall'esito actor:

- `INTERMEDIATE_OUTPUT` torna direttamente all'actor, ma Trajectory osserva la liveness;
- `PROPOSE_ACTION` senza contenuto fattuale attiva Trajectory; con contenuto fattuale attiva anche Evidence;
- `NEEDS_REPLAN` attiva Trajectory per liveness e convergenza;
- `STEP_COMPLETED` attiva Evidence e Trajectory;
- un `execution_candidate` governed-external non hard-denied attiva Evidence e Trajectory, poi l’ammissione staged del Judge.

Ai confini governati, Python:

1. costruisce fatti deterministici dal `TaskState`;
2. raccoglie Evidence e Trajectory quando richiesti;
3. invia un documento preliminare a OPA e legge `judge_admission`;
4. invoca il Judge soltanto quando i gate deterministici OPA lo ammettono;
5. invia l’assessment digest-bound nel documento finale;
6. valida e applica la direttiva restituita da OPA.

Python non possiede un secondo algoritmo di decisione finale.

Pilastri:

- static policy: vincoli deterministici e hard deny;
- evidence: integrità, lineage, binding candidate/snapshot/selection, copertura del
  burden dichiarato e ancoraggio letterale dei token estraibili;
- trajectory: storia aggregata della run, richieste esplicite di replan, recovery
  tecnica, revisioni e churn osservabile;
- judge: allineamento semantico dell'esatta proposta esterna;
- operational risk: shadow-only;
- behavioral: shadow-only.

Direttive principali:

- `continue`;
- `continue_read_only`;
- `request_more_evidence`;
- `replan`;
- `revise_action`;
- `ask_user_clarification`;
- `wait_human_review`;
- `stop`.

La governance non deve produrre cicli senza via d'uscita. In particolare non può
richiedere riferimenti a osservazioni quando l'azione proposta non dipende da
osservazioni pregresse. Le richieste di ulteriore prova devono derivare da un
difetto concreto, come un riferimento dichiarato ma inesistente.

Il numero di turni narrativi, l'assenza temporanea di tool call e la confidence
bassa non costituiscono degradazione. Dopo un replan, una richiesta equivalente
senza nuove osservazioni, tool outcome, step completati o cambiamenti
del piano viene trattata come churn e portata a HITL.

## 8. Effetti esterni, Judge e HITL

Un effetto esterno nasce come `ExternalOperationProposal` immutabile. La proposta
include famiglia, target, payload normalizzato, capability e `payload_digest`.

Flusso:

```text
actor
  -> normalizzazione proposta
  -> governance
  -> Judge sull'esatto digest, se richiesto
  -> OPA
  -> HITL, quando richiesto
  -> ExternalExecutionRequest
  -> executor
  -> ExternalExecutionReceipt
```

Per le email, l'approvazione umana dell'esatto digest è obbligatoria. Una modifica
umana crea un nuovo payload e un nuovo digest, che devono attraversare nuovamente
il confine governato.

L'executor controlla capability, schema, autorizzazione, digest e idempotency key.
Gli esiti sono `executed`, `failed`, `rejected_before_execution` o `indeterminate`.
Un esito indeterminato non viene ritentato automaticamente perché l'effetto remoto
potrebbe essere già avvenuto.

## 9. Behavioral read model

Il behavioral store è una memoria storica SQLite non autoritativa. Registra una
feature snapshot generica della run, calcola similarità, deviazione e novità e
produce una raccomandazione controfattuale shadow-only.

Non filtra per una tassonomia statica del task e non influenza la direttiva OPA.
La sua indisponibilità non blocca la run.

## 10. Persistenza, dashboard e resume

Il checkpointer LangGraph conserva lo stato necessario a `interrupt / resume`.
Gli artefatti per thread contengono trace, dashboard snapshot, summary e payload
HITL pendente. La dashboard ricostruisce lo stato dal checkpoint canonico, non da
una copia JSON potenzialmente obsoleta.

La review è legata a `request_id`, `operation_id` e digest. La dashboard valida la
decisione e la deposita nella decision inbox SQLite, ma non invoca LangGraph. Il
processo CLI originario mantiene una lease sul thread, reclama atomicamente il
comando e applica `Command(resume=...)` sul checkpoint ufficiale, mostrando la
continuazione nello stesso terminale. Un heartbeat indipendente mantiene valida
la lease durante attese lunghe di provider o tool. Dopo il resume, gli artefatti
vengono rigenerati dallo stato aggiornato.

La inbox è un canale control-plane idempotente, non una seconda memoria del
workflow. I suoi stati di consegna (`pending`, `claimed`, `consumed`, `failed`)
servono all'audit e alla recovery operativa. Dopo il submit, la dashboard elimina
la review dalle azioni pendenti e la mostra per due minuti sotto `Completate` come
elemento non interattivo; `review_completed` non significa che la run sia conclusa.

Se il processo originario termina, `main_layer2.py --attach-thread <thread_id>`
può acquisire una lease scaduta soltanto se il checkpoint è ancora in
`WAITING_HUMAN_REVIEW`. Il consumer reclama esclusivamente la decisione con lo
stesso `request_id`, inclusa una consegna rimasta `claimed` o `failed`. L'adapter
attivo del proof of concept è SQLite locale; un deployment multi-host deve fornire
un'implementazione condivisa dello stesso port.

## 11. Trace ed evaluation

Il trace live corrente è `layer2_trace.v6`. Espone:

- runtime e intake;
- piano completo e cursore;
- ultimo turno actor;
- output, step eseguiti e scratchpad;
- tool call e relative osservazioni;
- passaggi governance e pilastri;
- HITL;
- comunicazione ed esecuzione esterna;
- `termination_reason` nel runtime summary e nella proiezione di trace.

Il trace è una proiezione read-only del `TaskState`, non una seconda autorità.
Solo l'evaluation offline può normalizzare trace storiche nei contratti correnti.

## 12. Invarianti sintetiche

- Il planner possiede il piano; il runtime non lo riscrive.
- L'actor vede il piano completo e committa soltanto lo step corrente.
- Le prove runtime derivano da azioni concrete, non dalla formulazione del task.
- Gli adapter descrivono ciò che hanno osservato; il runtime non inventa semantica.
- I riferimenti dichiarati devono essere validi, ma non sono obbligatori per ogni
  tipo di azione.
- OPA è l'unica autorità della direttiva.
- Il Judge valuta semantica e digest, non instrada il grafo.
- Il Judge vede l’intera base semantica osservata attraverso il JSON deterministico
  e persistito `judge_evidence_bundle.v1`, non ricerca autonomamente la run, può
  citare soltanto gli `evidence_id` ammessi e non è limitato alla selezione actor;
  narrazione, contatori di processo e content digest citabili restano esclusi.
- HITL autorizza un nuovo effetto esterno soltanto quando OPA ha aperto
  `capability_approval_required`; gli altri gate restano correttivi o abortivi.
- Receipt e osservazioni restano distinti.
- Trace, dashboard e behavioral memory non sostituiscono `TaskState`.
# Completion candidate e commit

`actor -> completion candidate -> OPA -> commit` è distinto da
`actor -> tool request -> observation -> actor`. La narrazione non ha un edge
proprio: se non contiene un'azione, artifact, completion, replan o una pausa
bloccante viene gestita dal recovery. Evidence deriva solo da input autorevoli
o capability eseguite; rejection e circuit-open sono eventi osservabili ma non
evidence.

Per le comunicazioni la proposta esatta può essere materializzata dal runtime
all'ingresso di governance, dopo che l'actor ha creato il candidate. In quel
singolo boundary governance sigilla atomicamente operation id/digest e digest
della selezione Evidence nel candidate ancora unbound; non riscrive binding già
presenti. Prima del connector, `external_execution` ricontrolla candidate,
snapshot, selezione e owner step. Una receipt connector non coerente viene
persistita come `indeterminate` e instradata alla riconciliazione, non lasciata
esplodere dopo un possibile effetto esterno.
