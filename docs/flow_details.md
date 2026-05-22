# Dettaglio Del Flusso Runtime

Questo documento descrive il flusso reale del runtime Layer 2 così come è implementato oggi nel repository.
Non è una vista “marketing” e non è una semplificazione user-facing.
È una spiegazione operativa, pensata per capire:

- che cosa entra nel runtime
- che cosa fa ogni nodo
- che cosa viene scritto nello stato
- come vengono calcolati i segnali di governance
- quali soglie vengono usate
- che cosa succede quando una soglia viene superata
- come lavora la memoria behavioral
- quando interviene il judge
- come tutto questo si combina fino all’output finale

L’obiettivo è semplice: leggere questo file e capire davvero, dall’inizio alla fine, che cosa succede.

## 1. Vista Generale Del Flusso

La slice attiva del runtime è questa:

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

L’idea di fondo è:

1. Layer 1 trasforma il testo utente in un input strutturato.
2. Layer 2 costruisce `TaskState`.
3. Il planner crea un piano semantico.
4. L’actor lavora su uno step alla volta.
5. Ogni risultato dell’actor passa obbligatoriamente dalla governance.
6. Se l’actor propone un tool, il tool non parte subito: prima deve passare dalla governance.
7. Se il tool parte, il risultato viene trasformato in evidenza esplicita.
8. L’actor riparte con quella nuova evidenza.
9. Se serve, il runtime può:
   - chiedere più evidenza
   - fare replan
   - fermarsi per human review
   - abortire
10. A fine giro, il runtime persiste trace, snapshot, dashboard data e memoria behavioral.

## 2. I Principi Che Regolano Tutto Il Flusso

Ci sono alcuni principi forti che condizionano il comportamento di tutto il sistema:

- il planner produce solo piano semantico, non tool call concrete
- l’actor lavora solo sullo step corrente
- i tool passano sempre dal Tool Gateway
- la governance è sempre in mezzo tra actor e azione successiva
- dynamic policy, behavioral e judge non possono mai rendere il sistema più permissivo della static policy
- la trace esportata è derivata da `TaskState`, non è una seconda fonte di verità
- la memoria behavioral è un read model SQLite, non la fonte di verità primaria
- HITL usa `interrupt / resume` tipizzato

Questo vuol dire che il sistema è costruito per essere leggibile, spiegabile e auditabile, non per “correre” in modo opaco.

## 2.1. Mappa Dei File: Chi È Responsabile Di Cosa

Se vuoi capire la run al 100%, devi sapere anche dove vive ogni responsabilità.

- `src/layer2_runtime_governance/graph.py`
  - costruisce il grafo LangGraph
  - decide i nodi e il routing

- `src/layer2_runtime_governance/nodes.py`
  - contiene la logica eseguibile dei nodi
  - qui avvengono le mutazioni reali di `TaskState`

- `src/layer2_runtime_governance/state.py`
  - definisce il contratto centrale `TaskState`
  - definisce enum, step, validation, HITL ed external communication

- `src/layer2_runtime_governance/actor_contract.py`
  - definisce ciò che l’actor riceve
  - definisce ciò che l’actor può restituire

- `src/layer2_runtime_governance/tool_contract.py`
  - definisce request, result ed evidence tipizzate

- `src/layer2_runtime_governance/governance.py`
  - costruisce il policy input
  - applica static policy
  - combina tutti i layer di controllo

- `src/layer2_runtime_governance/oversight.py`
  - implementa la dynamic policy numerica

- `src/layer2_runtime_governance/behavioral.py`
  - legge e scrive il read model SQLite
  - calcola similarity, novelty, deviation e raccomandazione behavioral

- `src/layer2_runtime_governance/judge.py`
  - decide se il judge deve partire
  - costruisce il payload di valutazione semantica

- `src/layer2_runtime_governance/tool_gateway.py`
  - esegue davvero i tool ammessi
  - normalizza i risultati backend

- `src/layer2_runtime_governance/trace.py`
  - esporta la vista osservabile della run

La separazione corretta da tenere a mente è questa:

- `state.py` dice che cosa può esistere
- `graph.py` dice dove si può andare
- `nodes.py` dice che cosa cambia davvero
- i moduli governance decidono se si può proseguire o no

## 2.2. Le Quattro Strutture Da Tenere Sempre In Testa

Per capire il sistema senza confusione, conviene distinguere sempre queste quattro strutture:

1. `IntakeResult`
   - è l’input canonico del Layer 2

2. `TaskState`
   - è la verità runtime viva

3. `ActorTask`
   - è la vista congelata che l’actor riceve

4. `GovernanceDecisionTrace`
   - è il record canonico della decisione di governance

Se confondi questi quattro livelli, perdi subito il controllo del flusso.

## 3. Ingresso: Da Layer 1 A Layer 2

Il Layer 2 non lavora direttamente sul testo grezzo dell’utente.
Lavora su `IntakeResult`, cioè il contratto canonico prodotto da Layer 1.

Concettualmente l’input è questo:

```json
{
  "structured_task": {
    "task_spec": {
      "goal": "...",
      "context": "...",
      "constraints": ["..."],
      "domain_type": "...",
      "domain_label": "...",
      "sensitivity": "...",
      "success_criteria": ["..."]
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
    "request_id": "...",
    "warnings": [],
    "missing_information": []
  }
}
```

Punti importanti:

- `confidence_threshold` arriva da Layer 1 e viene riusato nel runtime
- `max_steps` limita il numero massimo di step semantici
- `max_tool_calls` limita le chiamate tool, se non è `null`
- `read_only_mode` è una regola di policy, non una preferenza dell’actor

## 4. Bootstrap: Nascita Di `TaskState`

Il nodo `bootstrap_from_intake` costruisce il `TaskState` iniziale.
Questo è il contratto centrale del runtime.

Qui vengono inizializzati:

- stato runtime corrente
- piano
- cursore dello step corrente
- storico evidenze
- scratchpad audit-safe
- step validations
- storico static policy
- storico dynamic policy
- storico behavioral
- storico judge
- storico governance
- storico human review
- stato `external_communication`, se rilevante
- contatori di actor turn e tool call
- identificativo del thread runtime

All’inizio il runtime non ha ancora pianificato né agito.

## 4.1. Anatomia Completa Di `TaskState`

Per capire davvero una run, devi conoscere la struttura interna di `TaskState`.

### 4.1.1 Blocchi Principali

`TaskState` contiene questi gruppi di campi:

- handoff da Layer 1
  - `intake_payload`
  - `task_spec`
  - `execution_policy`
  - `intake_metadata`

- stato runtime generale
  - `current_state`
  - `runtime_thread_id`
  - `step_index`
  - `step_history`
  - `runtime_flags`
  - `bootstrap_completed`
  - `human_review_resume_state`
  - `external_communication`

- snapshot planner
  - `plan_steps`
  - `current_plan_step_index`
  - `planner_rationale`
  - `planner_name`
  - `planner_prompt_version`

- snapshot actor
  - `actor_result`
  - `actor_name`
  - `actor_prompt_version`
  - `executed_steps`
  - `final_output_text`
  - `actor_turn_count`
  - `scratchpad`
  - `step_validations`

- snapshot tool loop
  - `current_tool_request`
  - `last_tool_result`
  - `tool_results_history`
  - `observed_evidence`
  - `tool_call_count`

- snapshot governance
  - `last_static_policy_assessment`
  - `static_policy_history`
  - `last_dynamic_policy_assessment`
  - `dynamic_policy_history`
  - `last_behavioral_assessment`
  - `behavioral_history`
  - `last_judge_assessment`
  - `judge_history`
  - `last_governance_decision`
  - `governance_history`

- snapshot HITL
  - `pending_human_review_request`
  - `last_human_review_request`
  - `human_review_history`
  - `last_human_review_decision`

### 4.1.2 Il Campo Più Importante: `current_state`

I valori possibili sono:

- `planning`
- `acting`
- `ready_next_step`
- `ready_tool_execution`
- `replanning`
- `waiting_human_review`
- `completed`
- `aborted`

Il routing del grafo dipende quasi tutto da questo campo.

### 4.1.3 `step_history` Non È Il Piano

`step_history` registra i passaggi dei nodi runtime.
Ogni record contiene:

- `step_index`
- `runtime_state`
- `node_name`
- `summary`

Quindi ti serve per rispondere a questa domanda:

```text
quali nodi sono stati attraversati davvero, in quale ordine, e in quale stato?
```

Non va confuso con `plan_steps`.

### 4.1.4 `plan_steps` Contro `executed_steps`

- `plan_steps`
  - è il piano vivo corrente
  - contiene anche step pending o bloccati

- `executed_steps`
  - contiene solo il riassunto degli step completati davvero

### 4.1.5 `actor_result` Contro `final_output_text`

- `actor_result`
  - è l’ultimo output dell’actor, anche se provvisorio

- `final_output_text`
  - è l’output finale canonico promosso dal runtime

Questi due campi possono coincidere, ma non devono coincidere per forza.

## 4.2. Runtime Flags

Le `runtime_flags` attive oggi sono:

- `clarification_required`
- `incomplete_intake`
- `read_only_execution`
- `high_sensitivity`
- `extractability_limited`

Queste flag servono a condensare segnali di cautela che i nodi successivi possono leggere senza rifare tutta l’analisi.

## 4.3. Stati Degli Step Semantici

Ogni `PlanStep` può essere:

- `pending`
- `in_progress`
- `completed`
- `blocked`

Interpretazione pratica:

- `pending`
  - non ancora iniziato

- `in_progress`
  - step su cui l’actor sta lavorando

- `completed`
  - step chiuso con validation positiva

- `blocked`
  - step fermato per chiarimento, replan o blocco equivalente

## 4.4. Decisioni Di `StepValidation`

Le decisioni tipizzate che l’actor può segnalare sono:

- `complete_step`
- `continue_acting`
- `request_more_evidence`
- `ask_clarification`
- `needs_replan`

Questa è la cerniera tra output actor e controllo runtime.
L’actor non dice “vai al nodo X”.
Dice soltanto quale dovrebbe essere il prossimo tipo di avanzamento sullo step.

## 4.5. Contratto Actor: Che Cosa Riceve E Che Cosa Può Restituire

L’actor non riceve `TaskState` intero mutabile.
Riceve `ActorTask`, cioè una vista congelata.

### 4.5.1 Cosa Contiene `ActorTask`

- `task_spec`
- `execution_policy`
- `intake_metadata`
- `current_state`
- `runtime_flags`
- `plan_steps`
- `past_steps`
- `observed_evidence`
- `scratchpad`
- `step_validations`
- `last_tool_result`
- `tool_call_count`
- `remaining_tool_calls`
- `actor_turn_count`
- `current_plan_step_index`
- `current_plan_step`
- metadati del planner

### 4.5.2 Perché Esiste `ActorTask`

Serve a garantire tre cose:

- l’actor vede il contesto utile
- l’actor non muta direttamente `TaskState`
- il runtime può auditare che cosa l’actor sapeva in quel turno

### 4.5.3 Cosa Può Restituire L’Actor

`ActorResult` contiene:

- `plan_step_id`
- `result_type`
- `summary`
- `capability_mode`
- `external_impact`
- `output_text`
- `blocking_reason`
- `tool_request`
- `scratchpad_entry`
- `step_validation`

Vincoli chiave:

- se propone un’azione, deve usare `tool_request`
- se vuole completare davvero uno step con output locale, deve fornire `step_validation`
- non può mutare direttamente stato, piano o governance

## 4.6. Contratto Tool: Che Cosa È Ammesso

Il contratto tool è fortemente tipizzato.

### 4.6.1 `ToolRequest`

Campi principali:

- `plan_step_id`
- `tool_name`
- `reason`
- `query`
- `url`
- `text`
- `path`
- `expression`
- `timezone`
- `limit`
- `max_results`
- `extractive_goal`

### 4.6.2 `ToolExecutionResult`

Campi:

- `request`
- `success`
- `summary`
- `content`
- `sources`
- `error_message`

### 4.6.3 `ObservedEvidence`

Campi:

- `evidence_id`
- `plan_step_id`
- `title`
- `locator`
- `excerpt`
- `source_tool`
- `support_level`

`ObservedEvidence` è l’unità reale di grounding riutilizzabile dai turn successivi dell’actor.

## 4.7. Tool Surface Attuale

I tool name tipizzati attuali sono:

- `web_search`
- `fetch_url_text`
- `mcp.local_test.echo`
- `mcp.local_test.read_fixture`
- `mcp.local_test.text_stats`
- `mcp.local_test.calculate_expression`

## 4.8. Sequenza Esatta Di Una Tool Call

La sequenza corretta non è “actor chiama tool”.
È questa:

1. actor produce `PROPOSE_ACTION`
2. il runtime salva `current_tool_request`
3. la governance valuta la proposta
4. se la proposta passa, il runtime entra in `READY_TOOL_EXECUTION`
5. `tool_gateway` esegue davvero il tool
6. il risultato viene salvato in `last_tool_result` e `tool_results_history`
7. `observe_tool_result` crea `ObservedEvidence`
8. l’actor riparte con la nuova evidenza
9. la governance valuta anche il turno successivo

Questa separazione è il motivo per cui il sistema resta controllato.

## 5. Planner: Cosa Fa Veramente

Il planner genera `plan_steps` semantici.
Ogni step contiene:

- `step_id`
- `objective`
- `rationale`
- `expected_outcome`
- `status`

Il planner non deve:

- chiamare tool
- generare payload eseguibili
- trasformarsi in executor

### 5.1 Budget Degli Step

Dopo il piano, il runtime applica `_enforce_plan_step_budget`.
Se `max_steps = 5`, gli step oltre il quinto vengono tagliati.

### 5.2 Compattazione Dei Piani Troppo Decomposti

Se il task è del tipo “trova fonti ufficiali + fai una short summary”, il runtime può compattare un piano troppo lungo in 2 step:

1. identificare fonti ufficiali e raccogliere solo evidenza high-level sufficiente
2. produrre una short summary coerente con quel livello di evidenza

La compattazione parte solo se:

- il task parla di `source`, `sources`, `reference`, `references`, `official`
- il task parla di `short summary`, `brief summary`, `concise summary`, `short answer`
- il task non parla di `threshold`, `article-level`, `full legal text`, `compliance mapping`, `detailed analysis`
- il piano sembra una pipeline “sources -> extraction -> synthesis”

Questa regola serve a evitare over-engineering su task piccoli.

## 6. Actor: Come Si Muove

L’actor riceve un task costruito da `build_actor_task`.
Ha il piano come contesto, ma può agire solo sullo step corrente.

Può produrre questi tipi di esito:

- `PROPOSE_ACTION`
- `INTERMEDIATE_OUTPUT`
- `FINAL_OUTPUT`
- `ASK_CLARIFICATION`
- `BLOCKED`
- `NEEDS_REPLAN`

### 6.1 Cosa Succede Dopo L’Actor

Ogni turno actor:

1. incrementa `actor_turn_count`
2. salva `actor_result`
3. salva eventuale `tool_request`
4. salva eventuale `scratchpad_entry`
5. salva eventuale `step_validation`
6. aggiorna lo stato runtime preliminare
7. passa obbligatoriamente a `governance`

### 6.2 Come L’Actor Cambia Lo Stato Locale

La funzione `_record_actor_execution` gestisce il dettaglio.

Caso per caso:

- `ASK_CLARIFICATION`, `BLOCKED`, `NEEDS_REPLAN`
  - lo step corrente diventa `BLOCKED`
  - l’eventuale tool request viene azzerata

- `PROPOSE_ACTION`
  - lo step resta `IN_PROGRESS`
  - la tool request viene mantenuta

- output locale con `step_validation.next_action = CONTINUE_ACTING` o `REQUEST_MORE_EVIDENCE`
  - lo step resta `IN_PROGRESS`

- output locale con `step_validation.next_action = ASK_CLARIFICATION`
  - lo step diventa `BLOCKED`
  - lo stato va verso `WAITING_HUMAN_REVIEW`

- output locale con `step_validation.next_action = NEEDS_REPLAN`
  - lo step diventa `BLOCKED`
  - lo stato va verso `REPLANNING`

- output locale con `COMPLETE_STEP` + `objective_satisfied = true` + `evidence_sufficient = true`
  - lo step diventa `COMPLETED`
  - viene aggiunto a `executed_steps`
  - il cursore passa allo step successivo
  - se non ci sono altri step, l’output può diventare `final_output_text`

Se il completamento è dichiarato ma non soddisfa davvero i vincoli di validazione, il runtime tratta la cosa come lavoro ancora aperto.

## 7. Mappatura Actor -> Runtime State

Prima della governance il runtime imposta uno stato preliminare:

- `NEEDS_REPLAN` -> `REPLANNING`
- `ASK_CLARIFICATION` o `BLOCKED` -> `WAITING_HUMAN_REVIEW`
- `PROPOSE_ACTION` -> `READY_TOOL_EXECUTION`
- `COMPLETE_STEP` valido con step successivo -> `READY_NEXT_STEP`
- `COMPLETE_STEP` valido senza step successivo -> `COMPLETED`
- output locale non ancora sufficiente -> `ACTING`

Questo stato però non è ancora definitivo.
La governance può irrigidirlo.

## 8. Governance: Ordine Reale Dei Layer

Il nodo `governance` esegue sempre questi passaggi, in questo ordine:

1. costruisce `PolicyEvaluationInput`
2. esegue static policy
3. esegue dynamic policy
4. esegue behavioral
5. decide se invocare il judge
6. combina tutto in una `GovernanceDecisionTrace`
7. applica la decisione allo stato runtime
8. prepara eventuale payload HITL
9. persiste snapshot behavioral e judge

Questo è il cuore del sistema.

## 9. `PolicyEvaluationInput`: Lo Snapshot Canonico Della Governance

La governance non legge lo stato “a mano” in modo sparso.
Costruisce uno snapshot compatto e tipizzato:

- dominio e sensitivity della task
- profilo di policy
- confidence threshold
- read-only mode
- budget
- runtime state
- runtime flags
- step corrente
- tipo di risultato actor
- capability mode
- impatto esterno sì/no
- presenza output testuale
- nome del tool richiesto
- conteggi tool call, evidenza osservata e actor turn
- fase di `external_communication`

Questo snapshot viene usato come confine comune tra i layer di governance.

## 10. Static Policy: Primo Filtro Hard

La static policy passa da OPA via HTTP.
Produce:

- `policy_decision`
- `reason_codes`
- `summary`
- `policy_engine_name`
- `policy_bundle_version`

Le decisioni possibili sono:

- `allow`
- `allow_read_only`
- `ask_clarification`
- `require_human_review`
- `deny`

### 10.1 Da `policy_decision` A `policy_directive`

Il runtime converte la decisione OPA in una direttiva:

- `deny` -> `stop`
- `require_human_review` -> `wait_human_review`
- `ask_clarification` -> `ask_user_clarification`
- se l’actor ha emesso `NEEDS_REPLAN` -> `replan`
- se l’actor ha emesso `PROPOSE_ACTION` e OPA dice `allow_read_only` -> `continue_read_only`
- altrimenti -> `continue`

Questa è la prima decisione “forte”.
Tutto il resto può solo renderla più conservativa.

## 11. Dynamic Policy: I Tre Fattori Reali

La dynamic policy è implementata in `oversight.py`.
Calcola tre segnali deterministici:

1. `evidence_quality`
2. `operational_risk`
3. `trajectory_health`

Poi produce una raccomandazione:

- `continue`
- `continue_read_only`
- `request_more_evidence`
- `replan`
- `wait_human_review`

### 11.1 Threshold Configurati

File: `configs/layer2_oversight_policy.json`

```json
{
  "thresholds": {
    "min_actor_confidence": 0.65,
    "min_evidence_quality": 0.70,
    "max_operational_risk": 0.60,
    "min_trajectory_health": 0.60
  }
}
```

Queste sono le soglie che guidano le decisioni:

- se `actor confidence < 0.65`, il sistema inizia a considerare il risultato debole
- se `evidence_quality < 0.70`, l’evidenza non è abbastanza forte per un completion attempt
- se `operational_risk > 0.60`, la dynamic policy raccomanda human review
- se `trajectory_health < 0.60`, la dynamic policy raccomanda replan

## 12. Evidence Quality: Come Viene Calcolata Davvero

Il punteggio parte da `0.0`.
Vengono sommati contributi positivi.
Poi, se il support level disponibile non basta rispetto al bisogno dello step, il punteggio viene tagliato.

### 12.1 Pesi Configurati

```json
{
  "evidence_quality_weights": {
    "evidence_sufficient": 0.30,
    "objective_satisfied": 0.20,
    "actor_confidence": 0.20,
    "observed_evidence_present": 0.15,
    "source_locator_present": 0.10,
    "last_tool_success": 0.05
  }
}
```

### 12.2 Formula Effettiva

Il runtime aggiunge:

- `+0.30` se l’ultima validation rilevante dice `evidence_sufficient = true`
- `+0.20` se l’ultima validation rilevante dice `objective_satisfied = true`
- `+(0.20 * confidence)` se esiste una validation
- `+0.15` se esiste evidenza osservata “meaningful”
- `+0.10` se almeno una evidenza meaningful ha `locator`
- `+0.05` se l’ultimo tool result dello step è `success = true`

### 12.3 Che Cos’è “Meaningful Evidence”

Non tutta l’evidenza osservata vale.
La funzione `_meaningful_observed_evidence` non usa più una soglia grezza basata solo sulla lunghezza.
Ora guarda:

- che l’`excerpt` non sia vuoto
- che l’`excerpt` non sia un puro messaggio di stato
- il `support_level` dell’evidenza
- la forma del contenuto, per esempio:
  - shortlist di fonti con URL per `source_discovery`
  - estratto informativo abbastanza sostanzioso per `high_level_summary`
  - estratto più denso o più specifico per `detailed_extracted` e `primary_legal`

Quindi una stringa breve ma informativa può contare, mentre una stringa lunga ma puramente di stato no.

### 12.4 Penalizzazione Sul Support Level

Dopo la somma, il runtime confronta:

- support level disponibile
- support level richiesto

Se il support level disponibile è sotto quello richiesto, non viene semplicemente sottratto qualcosa.
Succede una cosa più dura:

```text
score = min(score, min_evidence_quality * 0.49)
```

Dato che `min_evidence_quality = 0.70`, il massimo diventa:

```text
0.70 * 0.49 = 0.343
```

Quindi, se il support level non basta, il punteggio non può superare `0.343`.
Di fatto viene classificato come debole.

### 12.5 Caso Speciale: Step Solo Locale

Se lo step richiede solo `LOCAL_ONLY` e la validation dice:

- `COMPLETE_STEP`
- `objective_satisfied = true`
- `evidence_sufficient = true`
- `confidence >= 0.65`

allora il runtime forza:

```text
score >= min_evidence_quality
```

cioè almeno `0.70`.

Questo serve a non punire task legittimamente locali che non richiedono fonti esterne.

### 12.6 Livelli Del Segnale

- `score >= 0.70` -> `strong`
- `score >= 0.35` e `< 0.70` -> `moderate`
- `score < 0.35` -> `weak`

## 13. Confronto Tra Support Level Disponibile E Richiesto

Questo è un punto centrale.
Il runtime non guarda solo “c’è evidenza?”.
Guarda se il tipo di evidenza è abbastanza forte per il tipo di claim che l’actor sta cercando di fare.

### 13.1 Ordine Dei Support Level

La scala, dal più debole al più forte, è:

1. `local_only`
2. `source_discovery`
3. `high_level_summary`
4. `detailed_extracted`
5. `primary_legal`

### 13.2 Come Si Calcola Il Livello Disponibile

Il livello disponibile è il massimo support level tra le evidenze observed che sono meaningful.
Se non c’è evidenza meaningful, il livello disponibile è `local_only`.

### 13.3 Come Si Calcola Il Livello Richiesto

Il livello richiesto è il massimo tra:

- ciò che richiede lo step
- ciò che richiede il testo di output dell’actor

#### Regole Dal Testo Dello Step

Esempi:

- se lo step è solo discovery di fonti -> `source_discovery`
- se lo step è una short summary conservativa -> `high_level_summary`
- se lo step parla di `threshold`, `article`, `deadline`, `full legal text` -> `primary_legal`
- se lo step parla di obblighi specifici, enforcement mechanism, entry into force, timeline -> `detailed_extracted`

#### Regole Dal Testo Di Output

Esempi:

- se l’output è solo shortlist di fonti con URL -> `source_discovery`
- se è summary high-level conservativa -> `high_level_summary`
- se contiene obblighi specifici come `must`, `shall`, `interoperability`, `data portability`, `self-preferencing` -> `detailed_extracted`
- se contiene date precise, anni, soglie, numeri utenti, turnover, `article X` -> `primary_legal` o almeno `detailed_extracted`

### 13.4 Significato Del Confronto

Se:

```text
rank(available) < rank(required)
```

allora il runtime considera che l’output sta chiedendo più precisione di quella che l’evidenza può supportare.

Questa è la base sia della dynamic policy sia del judge.

## 14. Operational Risk: Formula E Soglie

Il rischio operativo misura il rischio dell’intento corrente dell’actor.

### 14.1 Parametri Configurati

```json
{
  "risk_scores": {
    "tool_risk": {
      "low": 0.15,
      "medium": 0.45,
      "high": 0.75,
      "critical": 0.95
    },
    "sensitivity_multiplier": {
      "low": 1.0,
      "medium": 1.15,
      "high": 1.35,
      "critical": 1.6
    },
    "external_impact_additive": 0.25,
    "write_capability_additive": 0.30,
    "requires_human_review_additive": 0.40
  }
}
```

### 14.2 Formula Effettiva

1. prende il `tool_risk` dal registry del Tool Gateway
2. se il tool non è noto ma è richiesto, usa `medium = 0.45`
3. prende il moltiplicatore della `task_sensitivity`
4. calcola:

```text
score = base_risk * sensitivity_multiplier
```

5. poi aggiunge:

- `+0.25` se `external_impact = true`
- `+0.30` se `capability_mode = write`
- `+0.40` se il tool è marcato `requires_human_review`

6. infine clampa tra `0.0` e `1.0`

### 14.3 Livelli

- `score > 0.60` -> `high`
- `score >= 0.30` e `<= 0.60` -> `medium`
- `score < 0.30` -> `low`

### 14.4 Che Cosa Succede Se Supera La Soglia

Se:

```text
operational_risk.score > 0.60
```

la dynamic policy raccomanda direttamente:

```text
wait_human_review
```

Questa è la regola più forte del ramo dynamic.

## 15. Trajectory Health: Come Si Calcola Davvero

`trajectory_health` misura se il runtime sta procedendo in modo sano o se sta degenerando.

Il punteggio parte da:

```text
score = 1.0
```

Poi vengono sottratte penalità.

### 15.1 Parametri Configurati

```json
{
  "trajectory_penalties": {
    "max_actor_turns_per_step": 5,
    "max_replans": 2,
    "actor_turn_overage_penalty": 0.10,
    "completion_attempt_turn_overage_scale": 0.50,
    "failed_tool_result_penalty": 0.20,
    "needs_replan_penalty": 0.25,
    "low_confidence_uncertainty_penalty": 0.20
  }
}
```

### 15.2 Turn Overrun Sullo Step Corrente

Il runtime conta i turn del solo step valutato.
Per farlo usa in ordine:

1. i `turn_index` nello scratchpad dello step
2. altrimenti il numero di validations dello step
3. altrimenti assume 1 se c’è actor result su quello step

Se i turn dello step superano `5`, calcola:

```text
actor_turn_overage = current_step_turn_count - 5
score -= actor_turn_overage * 0.10
```

#### Ammorbidimento Se Sembra Un Completamento Valido

Se c’è una validation che dice:

- `COMPLETE_STEP`
- `objective_satisfied = true`
- `evidence_sufficient = true`
- `confidence >= 0.65`

allora la penalità per overrun viene dimezzata:

```text
turn_overage_penalty = 0.10 * 0.50 = 0.05
```

Questo evita di punire troppo un completamento credibile arrivato un po’ tardi.

### 15.3 Tool Failure

Se l’ultimo tool result rilevante per quello step è fallito:

```text
score -= 0.20
```

### 15.4 Segnale Di Replan

Se:

- la validation dice `NEEDS_REPLAN`
- oppure l’actor result stesso è `NEEDS_REPLAN`

allora:

```text
score -= 0.25
```

### 15.5 Residual Uncertainty A Bassa Confidenza

Se l’ultima validation rilevante contiene:

- `residual_uncertainty`
- `confidence < 0.65`

allora:

```text
score -= 0.20
```

### 15.6 Limite Replan Totali

Il runtime conta i replan reali leggendo `step_history` e contando i record con `node_name == "replanner"`.

Se il conteggio è:

```text
>= max_replans
```

cioè `>= 2`, allora:

```text
score = 0.0
```

e viene aggiunto il reason code `REPLAN_LIMIT_EXCEEDED`.

Questa è una condizione molto dura.

### 15.7 Livelli

- `score >= 0.60` -> `healthy`
- `score >= 0.30` e `< 0.60` -> `watch`
- `score < 0.30` -> `degraded`

## 16. Come La Dynamic Policy Decide La Direttiva Finale

Dopo aver calcolato i tre fattori, la dynamic policy applica queste regole, in quest’ordine:

1. se `operational_risk.score > 0.60` -> `wait_human_review`
2. se c’è `REPLAN_LIMIT_EXCEEDED` -> `wait_human_review`
3. se `trajectory_health.score < 0.60` -> `replan`
4. se è un completion attempt e:
   - `evidence_quality.score < 0.70`
   - la validation segnala evidenza debole
   allora -> `request_more_evidence`
5. se l’actor result è `PROPOSE_ACTION` -> `continue_read_only`
6. altrimenti -> `continue`

### 16.1 Che Cos’è Un Completion Attempt

Per la dynamic policy sono completion attempt:

- `FINAL_OUTPUT`
- `INTERMEDIATE_OUTPUT`

### 16.2 Quando La Validation Segnala Evidenza Debole

La funzione `_validation_signals_weak_evidence` richiede:

- esista una validation
- `next_action == COMPLETE_STEP`
- e almeno una di queste:
  - `evidence_sufficient = false`
  - `confidence < 0.65`
  - support level disponibile sotto il support level richiesto

Quindi la dynamic policy non blocca ogni output intermedio.
Interviene soprattutto sui tentativi di chiusura fragili.

## 17. Behavioral: Scopo Reale

La behavioral non legge “testi lunghi” delle run passate.
Legge un read model strutturato in SQLite.

Scopo:

- trovare run storiche comparabili
- misurare quanto la run corrente assomiglia a loro
- capire quanto devia
- capire quanto è nuova
- usare tutto questo per una raccomandazione conservativa

Le raccomandazioni behavioral possibili sono:

- `continue`
- `request_more_evidence`
- `replan`
- `wait_human_review`

Non restituisce mai `stop`.

## 18. Behavioral: Dove Sta E Cosa Salva

Path di default:

```text
artifacts/runtime/behavioral/behavioral.sqlite3
```

Tabelle principali:

- `runs`
- `steps`
- `tool_calls`
- `governance_decisions`
- `dynamic_signals`
- `behavioral_features`
- `judge_assessments`

La behavioral viene persistita dopo ogni passaggio di governance.

## 19. Behavioral: Prima Query E Soglia Di Attivazione

La prima query è:

```sql
SELECT COUNT(*) FROM runs WHERE thread_id <> ?
```

Serve a capire quanta storia c’è, escludendo la run attuale.

Se `historical_run_count < 20`, il layer behavioral non entra nel vero scoring.
Restituisce un assessment “foundation only”.

In pratica:

- dice quante run storiche esistono
- annota che la history è sotto soglia
- raccomanda `continue`

Questa è una soglia importante:

```text
meno di 20 run storiche = niente similarity/deviation scoring vero
```

## 20. Behavioral: Selezione Dei Candidate Runs

Se la history è sufficiente, la behavioral costruisce `current_profile` dalla run attuale.

Il profilo include:

- `domain_type`
- `sensitivity`
- `policy_profile_name`
- presenza di `external_communication`
- `step_count`
- `actor_turn_count`
- `tool_call_count`
- `replan_count`
- `review_count`
- `abort_count`
- `max_evidence_support_level`
- set di tool usati
- distribuzione delle direttive di governance
- distribuzione delle validation

### 20.1 Query “Strict”

Prima prova una query stretta:

```sql
SELECT ...
FROM runs
WHERE thread_id <> ?
  AND domain_type = ?
  AND sensitivity = ?
  AND policy_profile_name = ?
ORDER BY has_external_communication = ? DESC, thread_id DESC
LIMIT 200
```

Questa cerca run con:

- stesso dominio
- stessa sensitivity
- stesso policy profile

Se trova almeno 20 candidate, usa queste.

### 20.2 Query Di Fallback

Se la query stretta produce meno di 20 candidate, allarga:

```sql
SELECT ...
FROM runs
WHERE thread_id <> ?
  AND domain_type = ?
  AND has_external_communication = ?
ORDER BY thread_id DESC
LIMIT 300
```

Qui rilassa:

- sensitivity
- policy profile

ma mantiene:

- stesso domain type
- stessa presenza o assenza di external communication

## 21. Behavioral: Caricamento Delle Feature Per I Candidate

Una volta trovati i thread id candidati, la behavioral carica:

- mappa delle feature da `behavioral_features`
- set dei tool usati da `tool_calls`
- distribuzione governance da `behavioral_features`
- distribuzione validation da `behavioral_features`

Questo serve a confrontare la run corrente con run storiche non solo per metadati, ma anche per forma del comportamento.

## 22. Similarity Score: Formula Reale

Per ogni candidate run viene calcolato uno score tra `0.0` e `1.0`.
La formula è una somma pesata di più confronti.

### 22.1 Pesi Della Similarità

```text
0.10 * match(domain_type)
0.10 * match(sensitivity)
0.10 * match(policy_profile_name)
0.05 * match(has_external_communication)
0.09 * ratio_similarity(step_count)
0.08 * ratio_similarity(actor_turn_count)
0.08 * ratio_similarity(tool_call_count)
0.05 * ratio_similarity(replan_count)
0.05 * ratio_similarity(review_count)
0.05 * ratio_similarity(abort_count)
0.10 * support_level_similarity(max_evidence_support_level)
0.10 * jaccard_similarity(tool_usage)
0.03 * distribution_similarity(governance_distribution)
0.02 * distribution_similarity(validation_distribution)
```

Poi lo score viene clampato tra `0.0` e `1.0`.

### 22.2 Significato Dei Singoli Confronti

`_binary_match`

- restituisce `1.0` se uguale
- `0.0` se diverso

`_ratio_similarity`

- se entrambi zero -> `1.0`
- altrimenti:

```text
1 - min(abs(current - historical) / max(current, historical, 1.0), 1.0)
```

Più i valori quantitativi si assomigliano, più si avvicina a 1.

`_support_level_similarity`

- confronta la distanza tra i rank dei support level
- distanza zero -> similarità alta
- distanza grande -> similarità bassa

`_jaccard_similarity`

```text
|intersezione tool| / |unione tool|
```

Serve a capire se il pattern di tool usati assomiglia davvero.

`_distribution_similarity`

- confronta due distribuzioni sparse normalizzate
- guarda quanto differiscono le frequenze relative di direttive e validation

## 23. Come Vengono Estratti I Match Migliori

Dopo il calcolo, i candidati vengono ordinati per score decrescente.

Poi si usano:

- top 5 come `top_summary`
- top 10 come `top_baseline`

Da qui nascono:

- `matched_run_count`
- `matched_run_ids`
- `historical_similarity_score`
- `novelty_score`
- `behavior_deviation_score`
- `review_rate`

## 24. Historical Similarity, Novelty E Deviation

### 24.1 Historical Similarity

È semplicemente il miglior score tra i candidati top.

Se non ci sono score, è `null`.

### 24.2 Novelty Score

Usa i primi 3 similarity score:

```text
novelty = 1 - media(similarity_scores_top3)
```

Quindi:

- similarity alta -> novelty bassa
- similarity bassa -> novelty alta

### 24.3 Behavior Deviation

Confronta la run corrente con la media del `top_baseline` sulle feature:

- `step_count`
- `actor_turn_count`
- `tool_call_count`
- `replan_count`
- `review_count`
- `abort_count`

Per ogni feature:

```text
normalized_deviation = abs(current - baseline_mean) / max(baseline_mean, 1.0)
```

Poi:

- `deviation_driver` = feature con deviazione più alta
- `behavior_deviation_score` = media delle deviazioni delle feature

Anche qui il risultato viene clampato tra `0.0` e `1.0`.

### 24.4 Review Rate

Sul `top_baseline`, conta quante run hanno avuto almeno una review:

```text
review_rate = review_runs / len(top_baseline)
```

## 25. Behavioral: Regole Vere Della Direttiva

La funzione `_recommend_behavioral_directive` applica queste regole, in quest’ordine.

### 25.1 Regola 1: Alta Novelty

Se:

- `novelty_score >= 0.70`
- e il dynamic signal non è forte

allora:

```text
request_more_evidence
```

Qui “dynamic signal non forte” significa, in sostanza:

- dynamic non assente e non chiaramente solido
- oppure evidence quality non `strong`

### 25.2 Regola 2: Bassa Similarità Storica Su Completion Attempt Debole

Se:

- `historical_similarity_score < 0.35`
- e l’actor sta tentando `INTERMEDIATE_OUTPUT` o `FINAL_OUTPUT`
- e il support level disponibile è tra:
  - `local_only`
  - `source_discovery`
  - `high_level_summary`

allora:

```text
request_more_evidence
```

Questa regola dice: “stai chiudendo in un modo poco simile alla storia, con evidenza non molto forte”.

### 25.3 Regola 3: Deviazione Molto Alta

Se:

```text
behavior_deviation_score >= 0.80
```

allora:

```text
wait_human_review
```

Questa è una soglia forte.

### 25.4 Regola 4: History Con Molte Review + Dynamic Non Sano

Se:

- `review_rate >= 0.50`
- e i segnali dynamic non sono healthy

allora:

```text
wait_human_review
```

“Dynamic non healthy” significa almeno una di queste:

- direttiva dynamic già conservativa
- trajectory non `healthy`
- operational risk `medium` o `high`
- evidence quality non `strong`

### 25.5 Regola 5: Deviazione Di Traiettoria

Se:

- `behavior_deviation_score >= 0.60`
- e `deviation_driver` è uno tra:
  - `actor_turn_count`
  - `replan_count`
  - `tool_call_count`

allora:

```text
replan
```

### 25.6 Default

Se nessuna delle regole sopra scatta:

```text
continue
```

## 26. Judge: Quando Parte Davvero

Il judge non gira sempre.
Prima viene calcolato se deve essere invocato.

### 26.1 Quando Non Parte Sicuramente

Se la static policy ha già deciso:

- `deny`
- `require_human_review`
- `ask_clarification`

allora il judge non parte.

Motivo: la decisione hard è già presa.

### 26.2 Trigger Di Invocazione

Il judge parte se esiste almeno una flag tra queste:

- escalation già suggerita dalla dynamic policy
- escalation già suggerita dalla behavioral
- task in fase `external_draft_review`
- mismatch tra precisione dell’output e support level disponibile
- fragile completion attempt
- output locale basato solo su `source_discovery`

### 26.3 Precision Mismatch

Il judge vede mismatch se:

```text
rank(available_support_level) < rank(required_support_level)
```

### 26.4 Borderline Completion

Il judge considera fragile un completion attempt se:

- actor result è `INTERMEDIATE_OUTPUT` o `FINAL_OUTPUT`
- e almeno una di queste:
  - manca la validation
  - `objective_satisfied = false`
  - `evidence_sufficient = false`
  - `confidence < execution_policy.confidence_threshold`
  - support level disponibile non supera `high_level_summary`

### 26.5 Output Su Solo Source Discovery

Se l’actor produce output locale e l’evidenza disponibile è solo `source_discovery`, il judge parte.

## 27. Judge: Che Input Riceve

Se parte, il judge riceve un payload molto ricco:

- task spec completa
- step corrente
- actor result
- step validation
- tutte le observed evidence
- static policy
- dynamic policy
- behavioral
- external communication
- parte rilevante del policy input
- support level disponibile e richiesto
- flags di invocazione

Quindi il judge non giudica “al buio”.
Giudica uno snapshot già strutturato.

## 28. Governance Combiner: La Regola Finale

Dopo static, dynamic, behavioral e judge, il runtime li combina.

### 28.1 Precedenza Hard

Se la static policy ha deciso:

- `deny` -> `stop`
- `require_human_review` -> `wait_human_review`
- `ask_clarification` -> `ask_user_clarification`

queste priorità vincono subito.

### 28.2 Candidati Alla Direttiva Finale

Se non c’è un caso hard, il combiner mette insieme:

- `static_policy.policy_directive`
- `dynamic_policy.recommended_directive`
- `behavioral.behavioral_recommended_directive`
- `judge.recommendation`

Tutte vengono mappate in `GovernanceDirective`.

### 28.3 Ranking Di Conservatività

Il ranking è:

- `continue` -> 0
- `continue_read_only` -> 0
- `request_more_evidence` -> 1
- `replan` -> 2
- `wait_human_review` -> 3
- `ask_user_clarification` -> 4
- `stop` -> 5

Il combiner prende la direttiva più conservativa.

Però c’è una precisazione:

- se il massimo è tra `wait_human_review`, `replan`, `request_more_evidence`, restituisce quello
- altrimenti restituisce la direttiva statica originaria

Quindi `continue_read_only` non viene usato come escalation sopra `continue`.
È trattato come un vincolo statico di base, non come escalation adaptiva.

## 29. Applicazione Della Decisione Di Governance

Una volta ottenuta la `GovernanceDecisionTrace`, `_apply_governance_decision` cambia davvero lo stato runtime.

### 29.1 `STOP`

- `current_state = ABORTED`
- tool request cancellata
- HITL pending cancellato
- external communication, se esiste, passa a `BLOCKED`

### 29.2 `WAIT_HUMAN_REVIEW` o `ASK_USER_CLARIFICATION`

- `current_state = WAITING_HUMAN_REVIEW`
- viene derivato `human_review_resume_state`
- external communication, se esiste, passa a `HUMAN_REVIEW_REQUIRED`

### 29.3 `REPLAN`

- `current_state = REPLANNING`
- tool request cancellata
- pending HITL cancellato
- external communication torna a `EVIDENCE_COLLECTION`

### 29.4 `REQUEST_MORE_EVIDENCE`

Questo è un caso importante.
Il runtime:

- rimette lo step corrente in `IN_PROGRESS`
- ripristina il cursore su quello step
- rimuove da `executed_steps` l’eventuale step segnato come completato in modo fragile
- se `final_output_text` coincide con l’output fragile, lo cancella
- imposta `current_state = ACTING`
- azzera la tool request

Quindi il runtime “disfa” il completamento fragile e costringe a continuare il lavoro.

### 29.5 `PROPOSE_ACTION` Accettata

Se la decisione finale non è più conservativa e l’actor aveva proposto un tool:

- `current_state = READY_TOOL_EXECUTION`

### 29.6 Output Locale Accettato

Se l’actor aveva prodotto output locale e la governance non lo irrigidisce, il runtime lascia valere lo stato già costruito dall’actor.

## 30. Tool Gateway: Che Cosa Fa Di Preciso

Il Tool Gateway è il solo punto in cui una tool request viene eseguita davvero.

Fa queste cose:

1. verifica che ci sia una request pronta
2. verifica il budget tool
3. blocca richieste duplicate sullo stesso step
4. esegue il tool backend
5. normalizza il risultato in `ToolExecutionResult`
6. aggiorna contatori e stato

### 30.1 Budget Tool

Se `max_tool_calls` non è `null` e il conteggio è esaurito:

- non lancia eccezione
- crea un `ToolExecutionResult` fallito tipizzato
- lo mette nello storico
- torna in `ACTING`

### 30.2 Deduplicazione

Per evitare loop inutili, il runtime blocca richieste duplicate nello stesso step.

La fingerprint è:

- `(tool_name, normalized_url)` se esiste `url`
- `(tool_name, normalized_query)` se esiste `query`

Se trova una richiesta identica già eseguita nello stesso `plan_step_id`, la blocca.

Nota importante:

- per request basate solo su `text`, come alcune MCP locali, questa fingerprint può essere `null`
- quindi il blocco dei duplicati si applica soprattutto a URL e query

## 31. Observe Tool Result: Da Risultato A Evidenza

`observe_tool_result` prende `last_tool_result` e lo converte in `ObservedEvidence`.

### 31.1 Se Il Tool È Fallito

Se il tool fallisce:

- non crea nuova evidenza
- salva comunque una scratchpad observation
- lascia che l’actor reagisca esplicitamente al fallimento

### 31.2 Se Il Tool Ha Sorgenti

Se il tool restituisce `sources`, il runtime prova a creare una evidenza per ciascuna source.

Per ogni source:

- usa `source.snippet` come `excerpt`
- se c’è una sola source e manca snippet, usa `tool_result.content` o `summary`
- scarta excerpt non meaningful
- assegna `support_level`

### 31.3 Fallback Senza Sorgenti

Se non ci sono sorgenti strutturate ma c’è comunque contenuto significativo:

- crea una evidenza fallback
- usa come locator:
  - `url`, oppure
  - `query`, oppure
  - nome del tool

### 31.4 Scratchpad Observation

Dopo aver creato l’evidenza, il runtime aggiunge una scratchpad entry audit-safe con:

- turn index
- plan step id
- summary dell’osservazione
- riferimenti agli `evidence_id`

## 32. Classificazione Del Support Level Delle Evidenze

Il support level non è arbitrario.
Viene classificato con euristiche abbastanza esplicite.

### 32.1 Regole Per `web_search`

Se l’excerpt contiene indicatori high-level come:

- official pages
- overview
- purpose
- gatekeepers
- obligations
- prohibitions
- legislation
- summary

allora tende a diventare `high_level_summary`.

Se è solo discovery povera, resta `source_discovery`.

### 32.2 Regole Per `fetch_url_text`

Se il locator è `eur-lex.europa.eu` e non è una pagina `/summary/`, allora:

- `primary_legal`

Se il locator è una pagina summary/overview nota:

- `high_level_summary`

Se l’excerpt contiene almeno due marker granulari come:

- `article`
- `must`
- `shall`
- `required to`
- `prohibited`
- `obligations`
- `criteria`
- `threshold`
- `deadline`
- `interoperability`
- `data portability`

allora:

- `detailed_extracted`

### 32.3 Tool Non `fetch_url_text`

Se il tool non è `fetch_url_text` e non è `web_search`, il default tende a essere:

- `detailed_extracted`

Questo include i tool locali MCP che restituiscono contenuto già più strutturato.

## 33. Replanner: Quando Entra E Che Cosa Cambia

Il replanner entra quando la governance o l’actor portano il runtime in `REPLANNING`.

Il replanner:

- legge lo stato corrente
- genera `replacement_steps`
- mantiene immutati gli step già completati
- sostituisce solo step corrente e futuri

Regola chiave:

- gli step completati sono storia immutabile

Dopo il replan:

- il primo replacement step diventa `IN_PROGRESS`
- gli altri diventano `PENDING`
- `current_tool_request` viene azzerata
- `last_tool_result` viene azzerato
- lo stato torna ad `ACTING`

## 34. HITL: Come Parte E Come Riprende

Se il runtime entra in `WAITING_HUMAN_REVIEW`:

- costruisce `pending_human_review_request`
- salva `human_review_resume_state`
- può emettere un callback per la dashboard
- LangGraph va nel nodo `human_review`

Le decisioni tipizzate possono poi reindirizzare il runtime verso:

- planner
- actor
- tool gateway
- replanner
- finalize

Quindi la review non è un vicolo cieco.
È una pausa tipizzata con resume controllato.

## 35. Persistenza Behavioral E Judge Dopo Ogni Governance Pass

Dopo la governance, il runtime prova a fare due persist:

- `persist_behavioral_snapshot(...)`
- `persist_judge_snapshot(...)`

Queste persist non cambiano la decisione di governance.
Se falliscono, il runtime non si rompe: il codice le avvolge in `try/except` e continua.

Questo è coerente con il ruolo della behavioral:

- utile per memoria e analisi
- non autoritativa per il core execution path

## 36. Cosa Viene Salvato Nello Snapshot Behavioral

Per ogni thread, il runtime riscrive il materiale nelle tabelle:

- `runs`
- `steps`
- `tool_calls`
- `governance_decisions`
- `dynamic_signals`
- `behavioral_features`

Questo rende lo snapshot idempotente: si ricalcola e si sostituisce.

Tra i dati più importanti salvati:

- stato finale della run
- domain type
- sensitivity
- policy profile
- max steps e max tool calls
- actor turn count
- tool call count
- replan count
- review count
- abort count
- presenza di external communication
- max support level osservato
- stato di ogni step
- tool usati
- score dynamic
- distribuzioni governance e validation

## 37. Routing Del Grafo Dopo La Governance

Il grafo LangGraph fa routing in base al `current_state` già aggiornato dalla governance.

### 37.1 Se `READY_TOOL_EXECUTION`

Va a `tool_gateway`.

### 37.2 Se `REPLANNING`

Va a `replanner`.

### 37.3 Se `WAITING_HUMAN_REVIEW` Con Request Pending

Va a `human_review`.

### 37.4 Se `READY_NEXT_STEP`

Rientra subito in `actor` sullo step successivo.

### 37.5 Se `ACTING`

Rientra in `actor` sullo stesso step.

### 37.6 Altrimenti

Va a `finalize_bootstrap`.

## 38. Finalizzazione

`finalize_bootstrap` non inventa nulla di nuovo.
Segna semplicemente che la slice runtime corrente è arrivata a un punto stabile.

Fa queste cose:

- `bootstrap_completed = true`
- incrementa `step_index`
- aggiunge un ultimo `StepRecord`

Se lo stato è `ABORTED`, il summary finale dice esplicitamente che il runtime si è fermato dopo denial di governance.

Altrimenti il summary finale riporta:

- actor result finale
- runtime state
- actor turns
- tool calls

## 38.1. Come Leggere Una Run Già Eseguita Senza Perdersi

Se hai davanti `TaskState`, una trace JSON o un dump runtime, l’ordine migliore di lettura è questo:

1. `task_spec`
   - che task era davvero

2. `execution_policy`
   - con quali vincoli è partita la run

3. `runtime_flags`
   - quali segnali di cautela erano già attivi

4. `plan_steps`
   - qual era la traiettoria semantica

5. `step_history`
   - quali nodi sono stati eseguiti davvero

6. `actor_result`, `step_validations`, `scratchpad`
   - che cosa l’actor ha tentato nei vari turni

7. `tool_results_history` e `observed_evidence`
   - quali evidenze reali sono entrate nel loop

8. `static_policy_history`, `dynamic_policy_history`, `behavioral_history`, `judge_history`
   - come è stata valutata la run a ogni passaggio

9. `governance_history`
   - quale direttiva finale è stata scelta ogni volta

10. `executed_steps` e `final_output_text`
   - che cosa è stato davvero completato

Se salti questo ordine, rischi di guardare solo l’output finale e perdere il perché della traiettoria.

## 38.2. Come Leggere La Struttura Del Sistema

Se invece vuoi capire la struttura, l’ordine di lettura migliore del codice è:

1. `state.py`
2. `graph.py`
3. `nodes.py`
4. `actor_contract.py`
5. `tool_contract.py`
6. `governance.py`
7. `oversight.py`
8. `behavioral.py`
9. `judge.py`
10. `trace.py`

Questo ordine è il più vicino possibile alla logica reale della run.

## 38.3. Formula Mentale Corretta Del Runtime

La formula mentale più corretta è questa:

```text
Layer 2 non è un executor lineare.
È un ciclo controllato in cui:
  - il planner propone la traiettoria semantica
  - l’actor tenta di avanzare di uno step
  - la governance decide se quel tentativo è accettabile
  - i tool producono evidenza osservabile
  - la nuova evidenza torna all’actor
  - il sistema ripete finché non completa, replanna, va in review o abortisce
```

Se tieni in testa questa formula, la struttura del runtime diventa molto più facile da seguire.

## 39. Esempio Ragionato: Quando Scatta `request_more_evidence`

Caso:

- task di ricerca su DMA
- actor produce un `FINAL_OUTPUT`
- l’evidenza osservata è solo `source_discovery`
- l’output contiene dettagli temporali o obblighi specifici

Succede:

1. `required_support_level` sale almeno a `detailed_extracted`, a volte `primary_legal`
2. `available_support_level` resta `source_discovery`
3. `evidence_support_matches_step_need = false`
4. `evidence_quality` viene capata al massimo a `0.343`
5. se la validation tenta `COMPLETE_STEP`, la dynamic policy vede un fragile completion attempt
6. la dynamic policy raccomanda `request_more_evidence`
7. il judge probabilmente parte per `precision_support_mismatch`
8. il combiner prende la direttiva più conservativa
9. `_apply_governance_decision` ripristina lo step come `IN_PROGRESS`

Questo è il comportamento voluto:
il runtime non lascia chiudere con claim più precisi dell’evidenza disponibile.

## 40. Esempio Ragionato: Quando Scatta `wait_human_review`

Può succedere in almeno tre modi:

1. OPA lo impone direttamente
2. `operational_risk > 0.60`
3. behavioral segnala:
   - `behavior_deviation_score >= 0.80`
   - oppure `review_rate >= 0.50` con dynamic non healthy

In questi casi:

- la run entra in `WAITING_HUMAN_REVIEW`
- viene preparato il payload review
- il thread può essere ripreso in modo tipizzato

## 41. Esempio Ragionato: Quando Scatta `replan`

Può succedere se:

- `trajectory_health < 0.60`
- oppure la behavioral vede `behavior_deviation_score >= 0.60` con driver su:
  - actor turn count
  - replan count
  - tool call count
- oppure l’actor stesso produce `NEEDS_REPLAN`

In quel caso il sistema non insiste sulla traiettoria corrente.
La sostituisce.

## 42. Cosa Significa Davvero “Conservativo” In Questo Runtime

Nel repository “conservativo” non vuol dire solo “prudente”.
Vuol dire una cosa precisa:

- non aumentare la permissività rispetto alla static policy
- non permettere completamenti oltre il support level dell’evidenza
- non ignorare deviazioni forti dalla storia
- non far collassare task rischiose in execution automatica
- preferire richiesta di evidenza, replan o review quando il segnale è ambiguo

## 43. In Sintesi: Il Flusso End-To-End

Il percorso completo è questo:

```text
Task utente
  -> Layer 1 produce IntakeResult
  -> bootstrap costruisce TaskState
  -> planner crea plan_steps semantici
  -> actor lavora sul current step
  -> governance costruisce PolicyEvaluationInput
  -> static policy decide il vincolo hard
  -> dynamic policy calcola evidence_quality, operational_risk, trajectory_health
  -> behavioral confronta la run con la storia SQLite
  -> judge parte solo se ci sono segnali semantici fragili
  -> combiner prende la direttiva più conservativa compatibile con la static policy
  -> runtime applica la decisione
  -> se serve tool: Tool Gateway
  -> observe_tool_result trasforma il risultato in ObservedEvidence
  -> actor riparte con nuova evidenza
  -> eventuale replan o HITL
  -> finalizzazione
  -> trace export + artifact runtime + behavioral snapshot
```

## 44. Punto Più Importante Per Leggere Il Sistema

Se vuoi capire il comportamento reale del runtime, devi leggere sempre insieme questi quattro livelli:

1. che cosa l’actor sta cercando di fare
2. che support level richiede davvero quello che sta dicendo
3. che support level è davvero disponibile nelle evidenze osservate
4. quanto la traiettoria corrente assomiglia o devia dalla storia

Tutto il resto del sistema gira intorno a questo confronto.
