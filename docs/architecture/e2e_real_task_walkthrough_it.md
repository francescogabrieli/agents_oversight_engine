# Architettura End-to-End su una Task Reale

## Scopo del documento

Questo documento spiega, passo per passo, come una task reale attraversa l'architettura del progetto:

1. entra in Layer 1 come input grezzo;
2. viene trasformata nel JSON canonico di intake;
3. viene consegnata a Layer 2;
4. genera `TaskState`, `observed_evidence`, piano, output actor, decisioni di governance e richiesta HITL;
5. si ferma nel punto corretto del flow.

L'obiettivo non è dare una descrizione astratta, ma mostrare il comportamento reale del sistema su un caso concreto, con i payload e le trasformazioni che avvengono davvero nel codice.

---

## Caso di studio

Useremo una task reale del dominio `external_communication`, già emersa durante il lavoro sul runtime:

### Task operativa

```text
Draft an external email to the Acme Cloud operations team about an ongoing sensitive EU runtime incident affecting the customer notification pipeline, requesting specific manual checks and a status update within 60 minutes.
```

### Context hint

```text
Incident detected 09:12 CET on May 25, 2026; intermittent delivery failures for transactional notifications; still under investigation; no confirmed data loss; delayed processing observed for some EU tenants. Prefer official EU sources.
```

Questo esempio è particolarmente utile perché:

- esercita davvero il passaggio `Task Intake -> Bootstrap -> Planner -> Actor -> Governance -> Human Review`;
- mostra bene come nascono le `observed_evidence` a partire dal task intake;
- mostra una branch importante dell'architettura: il caso in cui non servono tool esterni perché il draft può essere prodotto localmente dai fatti già forniti dall'intake;
- mostra anche il gate di human review, che per `external_communication` è parte fondamentale del sistema.

---

## Mappa sintetica dell'architettura

Il percorso principale di questo run è il seguente:

```text
Raw input
  -> Layer 1 Intake
  -> IntakeResult
  -> Layer 2 bootstrap_from_intake
  -> TaskState iniziale
  -> observed_evidence iniziali da intake
  -> planner
  -> actor
  -> governance
  -> actor
  -> governance
  -> actor
  -> governance
  -> waiting_human_review
```

Il grafo reale di Layer 2, definito in `src/layer2_runtime_governance/graph.py`, è invece più generale:

```text
START
  -> bootstrap_from_intake
  -> planner
  -> actor
  -> governance
       -> actor
       -> tool_gateway -> observe_tool_result -> actor
       -> replanner -> actor
       -> human_review
       -> finalize_bootstrap
```

Nel caso di studio presente in questo documento:

- il ramo `tool_gateway` non viene preso;
- il ramo `replanner` non viene preso;
- il ramo `human_review` viene preso alla fine, perché il draft esterno è review-ready e deve essere approvato da un umano.

---

## Dove vive ogni passaggio nel codice

### Layer 1

- `src/main_layer1.py`: orchestrazione lineare e leggibile di Layer 1
- `src/layer1_intake/service.py`: servizi principali di intake
- `src/layer1_intake/models.py`: modelli canonici `TaskSpec`, `ExecutionPolicy`, `IntakeResult`

### Layer 2

- `src/main_layer2.py`: entrypoint end-to-end
- `src/layer2_runtime_governance/graph.py`: wiring del LangGraph
- `src/layer2_runtime_governance/nodes.py`: nodi runtime
- `src/layer2_runtime_governance/state.py`: `TaskState` e bootstrap delle evidenze da intake
- `src/layer2_runtime_governance/actor_contract.py`: payload consegnato all'actor
- `src/layer2_runtime_governance/governance.py`: policy statica e combinazione finale
- `src/layer2_runtime_governance/oversight.py`: policy dinamica
- `src/layer2_runtime_governance/hitl.py`: costruzione della richiesta di human review
- `src/layer2_runtime_governance/tool_contract.py`: contratto delle `ObservedEvidence`

### Script utile per capire il confine Intake -> Bootstrap

- `debug/debug_intake_to_bootstrap.py`

Questo script è utile perché ferma il flusso subito dopo il bootstrap e rende molto visibile come il JSON di intake diventa `TaskState`.

---

## Passo 1: nasce il comando di Layer 1

L'entrypoint reale end-to-end è `run_layer2_flow()` in `src/main_layer2.py`.

Se non si usa un demo profile, `run_layer2_flow()` costruisce un `IntakeCommand` e lo passa al servizio di Layer 1. Il comando è concettualmente questo:

```json
{
  "raw_task_text": "Draft an external email to the Acme Cloud operations team about an ongoing sensitive EU runtime incident affecting the customer notification pipeline, requesting specific manual checks and a status update within 60 minutes.",
  "context_hint": "Incident detected 09:12 CET on May 25, 2026; intermittent delivery failures for transactional notifications; still under investigation; no confirmed data loss; delayed processing observed for some EU tenants. Prefer official EU sources.",
  "policy_profile": null
}
```

### Cosa succede qui

- il testo grezzo viene ancora trattato come input esterno, non come stato runtime;
- il sistema non ha ancora deciso dominio, sensibilità, vincoli, policy o parse status;
- non esiste ancora alcuna evidenza.

---

## Passo 2: Layer 1 normalizza l'input

Il primo componente che lavora sul comando è `InputNormalizer` in `src/layer1_intake/service.py`.

### Cosa fa

- pulisce whitespace superflui;
- trasforma stringhe vuote in `None` per i campi opzionali;
- stabilizza il payload prima del parsing.

Nel nostro esempio il contenuto sostanziale non cambia, ma il sistema si assicura che la forma del comando sia canonica.

---

## Passo 3: il parser produce il `TaskSpec`

Il parsing semantico avviene dentro `TaskSpecBuilder.build()` in `src/layer1_intake/service.py`.

Il risultato atteso, nel caso di studio, è un `TaskSpec` come questo:

```json
{
  "goal": "Draft an external email to the Acme Cloud operations team about an ongoing sensitive EU runtime incident affecting the customer notification pipeline, requesting specific manual checks and a status update within 60 minutes.",
  "context": "Incident detected 09:12 CET on May 25, 2026; intermittent delivery failures for transactional notifications; still under investigation; no confirmed data loss; delayed processing observed for some EU tenants. Prefer official EU sources.",
  "constraints": [
    "Email addressed externally to the operations team at Acme Cloud",
    "Describe the incident timeline and observed impact accurately as stated",
    "Do not claim confirmed data loss",
    "Request manual verification of delivery queues and check retry backlog",
    "Ask to confirm whether customer-facing impact extended beyond the notification pipeline",
    "Request status report back to the incident lead within 60 minutes",
    "Prefer official EU sources"
  ],
  "domain_type": "external_communication",
  "domain_label": "Draft incident escalation email to operations (EU notification pipeline)",
  "sensitivity": "critical",
  "success_criteria": [
    "Email includes incident detection time/date, symptom description, and current investigation status",
    "Explicitly states no confirmed data loss while noting delayed processing for some EU tenants",
    "Lists the required operational checks (delivery queues, retry backlog) and the impact-scope confirmation question",
    "Requests a status update to the incident lead within 60 minutes",
    "Uses wording appropriate for a sensitive runtime incident and avoids unverified conclusions"
  ]
}
```

### Cosa succede qui

Il parser non sta ancora decidendo come eseguire la task. Sta creando il nucleo semantico della richiesta:

- qual è l'obiettivo;
- qual è il contesto;
- quali sono i vincoli;
- che tipo di dominio è;
- quanto è sensibile;
- quali criteri di successo devono essere rispettati.

### Output aggiuntivo di `TaskSpecBuilder`

Oltre al `TaskSpec`, `TaskSpecBuilder` produce:

- `parse_status`
- `warnings`
- `missing_information`

Nel nostro caso:

```json
{
  "parse_status": "complete",
  "warnings": [],
  "missing_information": []
}
```

Questo significa che Layer 1 considera la task sufficientemente completa per entrare in Layer 2 senza fermarsi prima per chiarimenti.

---

## Passo 4: viene risolta la `ExecutionPolicy`

Dopo il parsing, `PolicyResolver.resolve()` in `src/layer1_intake/service.py` seleziona la policy di esecuzione.

Nel caso di studio, la policy risultante è:

```json
{
  "policy_profile_name": "default",
  "confidence_threshold": 0.9,
  "budget": {
    "max_steps": null,
    "max_tool_calls": null
  },
  "read_only_mode": false
}
```

### Cosa succede qui

Questo è un punto architetturale importante:

- la policy non viene inventata dall'actor;
- la policy non viene lasciata implicita nel prompt;
- la policy è un oggetto canonico di Layer 1;
- Layer 2 la riceve già materializzata.

In altre parole: il sistema separa il significato della task dalle regole operative con cui la task potrà essere eseguita.

---

## Passo 5: nasce il JSON canonico di intake

`StructuredTask` e `IntakeMetadata` vengono assemblati e validati in `IntakeResultValidator`. Il risultato finale è `IntakeResult`.

Nel caso di studio, l'oggetto canonico è sostanzialmente questo:

```json
{
  "structured_task": {
    "task_spec": {
      "goal": "Draft an external email to the Acme Cloud operations team about an ongoing sensitive EU runtime incident affecting the customer notification pipeline, requesting specific manual checks and a status update within 60 minutes.",
      "context": "Incident detected 09:12 CET on May 25, 2026; intermittent delivery failures for transactional notifications; still under investigation; no confirmed data loss; delayed processing observed for some EU tenants. Prefer official EU sources.",
      "constraints": [
        "Email addressed externally to the operations team at Acme Cloud",
        "Describe the incident timeline and observed impact accurately as stated",
        "Do not claim confirmed data loss",
        "Request manual verification of delivery queues and check retry backlog",
        "Ask to confirm whether customer-facing impact extended beyond the notification pipeline",
        "Request status report back to the incident lead within 60 minutes",
        "Prefer official EU sources"
      ],
      "domain_type": "external_communication",
      "domain_label": "Draft incident escalation email to operations (EU notification pipeline)",
      "sensitivity": "critical",
      "success_criteria": [
        "Email includes incident detection time/date, symptom description, and current investigation status",
        "Explicitly states no confirmed data loss while noting delayed processing for some EU tenants",
        "Lists the required operational checks (delivery queues, retry backlog) and the impact-scope confirmation question",
        "Requests a status update to the incident lead within 60 minutes",
        "Uses wording appropriate for a sensitive runtime incident and avoids unverified conclusions"
      ]
    },
    "execution_policy": {
      "policy_profile_name": "default",
      "confidence_threshold": 0.9,
      "budget": {
        "max_steps": null,
        "max_tool_calls": null
      },
      "read_only_mode": false
    }
  },
  "metadata": {
    "parse_status": "complete",
    "parser_name": "openai_task_spec_parser",
    "prompt_version": "task_spec_v1",
    "schema_version": "1.0.0",
    "warnings": [],
    "missing_information": []
  }
}
```

### Cosa succede qui

Da questo momento in poi:

- Layer 1 ha finito;
- il confine semantico-operativo è chiuso;
- Layer 2 non deve reinterpretare il task grezzo;
- Layer 2 deve lavorare solo sul payload canonico.

Questa è una delle scelte più importanti dell'architettura.

---

## Passo 6: `IntakeResult` entra nel grafo di Layer 2

In `src/main_layer2.py`, `run_layer2_flow()` passa `intake_result` al LangGraph di Layer 2.

Il primo nodo del grafo è:

```text
bootstrap_from_intake
```

Implementazione:

- `src/layer2_runtime_governance/graph.py`
- `src/layer2_runtime_governance/nodes.py`

### Cosa succede qui

Il sistema non pianifica ancora e non scrive ancora alcun draft. Prima costruisce lo stato runtime canonico.

---

## Passo 7: `TaskState.from_intake()` costruisce il primo stato runtime

Il cuore di questo passaggio è `TaskState.from_intake()` in `src/layer2_runtime_governance/state.py`.

Il bootstrap produce un `TaskState` iniziale con questi elementi chiave:

```json
{
  "current_state": "planning",
  "runtime_flags": [
    "high_sensitivity"
  ],
  "bootstrap_completed": false,
  "plan_steps": [],
  "current_plan_step_index": null,
  "actor_turn_count": 0,
  "tool_call_count": 0,
  "observed_evidence": [],
  "external_communication": {
    "phase": "evidence_collection",
    "review_ready": false
  }
}
```

### Cosa succede davvero

`TaskState.from_intake()` fa quattro cose fondamentali:

1. copia dentro il runtime gli oggetti canonici di Layer 1:
   - `task_spec`
   - `execution_policy`
   - `intake_metadata`
   - `intake_payload`

2. decide lo stato iniziale del runtime:
   - `parse_status = complete` -> `current_state = planning`

3. aggiunge i flag runtime:
   - `sensitivity = critical` -> `high_sensitivity`

4. inizializza il subflow di `external_communication`:
   - `phase = evidence_collection`

### Nota importante

Per una task di `external_communication`, il runtime crea subito uno stato separato dedicato al draft esterno. Questo rende esplicito il confine fra:

- raccolta evidenze,
- bozza in costruzione,
- bozza review-ready,
- approvazione umana,
- eventuale esecuzione esterna futura.

---

## Passo 8: dal task intake nascono le prime `observed_evidence`

Questo è il passaggio chiave per capire l'architettura.

Le `observed_evidence` iniziali non arrivano dal planner, non arrivano dall'actor e non arrivano dai tool. Nascono direttamente in bootstrap tramite:

```text
TaskState.from_intake()
  -> _build_initial_observed_evidence_from_intake(task_spec)
```

Funzioni coinvolte:

- `src/layer2_runtime_governance/state.py`
  - `_build_initial_observed_evidence_from_intake`
  - `_append_intake_evidence_item`
  - `_split_intake_evidence_statements`
  - `_is_authoritative_intake_evidence_text`

### Regola architetturale

L'idea è questa:

- se Layer 1 ha già fornito fatti, vincoli e criteri affidabili;
- e se la task può essere svolta localmente senza cercare fonti esterne;
- allora quei fatti devono diventare subito evidenza esplicita del runtime.

In questo modo, il planner e l'actor non lavorano "a memoria": lavorano su evidenze osservate e tracciate.

---

## Passo 9: come l'intake viene spezzato in statement

`_build_initial_observed_evidence_from_intake()` prende questi campi del `TaskSpec`:

- `goal`
- `context`
- `constraints`
- `success_criteria`

### Per `goal` e `context`

Il testo viene spezzato in statement più piccoli con `_split_intake_evidence_statements()`.

Esempio sul `context`:

```text
Incident detected 09:12 CET on May 25, 2026;
intermittent delivery failures for transactional notifications;
still under investigation;
no confirmed data loss;
delayed processing observed for some EU tenants.
Prefer official EU sources.
```

diventa, concettualmente:

1. `Incident detected 09:12 CET on May 25, 2026`
2. `intermittent delivery failures for transactional notifications`
3. `still under investigation`
4. `no confirmed data loss`
5. `delayed processing observed for some EU tenants`
6. `Prefer official EU sources`

### Per `constraints` e `success_criteria`

Non c'è splitting semantico ulteriore: ogni item entra come candidato di evidenza.

---

## Passo 10: il filtro che decide cosa entra davvero come evidenza

Ogni statement passa in `_append_intake_evidence_item()`, che applica tre controlli:

### 1. Normalizzazione

- collassa gli spazi;
- scarta stringhe vuote.

### 2. Filtro di autorevolezza

`_is_authoritative_intake_evidence_text()` scarta testo puramente meta o troppo debole.

Esempio importante:

- `Prefer official EU sources` da sola non è un fatto operativo;
- quindi non deve trasformarsi automaticamente in evidenza fattuale.

In pratica, il runtime vuole tenere:

- fatti;
- limiti;
- obblighi operativi;
- criteri di successo;

e non vuole riempire `observed_evidence` con preferenze generiche o meta-istruzioni.

### 3. Deduplica

Ogni excerpt viene deduplicato case-insensitive. Se lo stesso contenuto compare più volte fra goal, context, constraint o success criterion, viene tenuto una sola volta.

---

## Passo 11: forma esatta di un'evidenza derivata da intake

Ogni item che supera il filtro diventa una `ObservedEvidence` con il contratto di `src/layer2_runtime_governance/tool_contract.py`.

Esempio:

```json
{
  "evidence_id": "intake:2",
  "plan_step_id": "intake",
  "title": "Task context",
  "locator": "intake://task_spec",
  "excerpt": "Incident detected 09:12 CET on May 25, 2026",
  "source_kind": "intake_provided",
  "source_tool": null,
  "support_level": "local_only"
}
```

### Significato dei campi

- `evidence_id`: identificatore stabile dentro il trace
- `plan_step_id = "intake"`: questa evidenza non è stata scoperta da uno step del piano, ma esisteva già nel payload iniziale
- `title`: da dove proviene semanticamente (`Task goal`, `Task context`, `Task constraint`, `Success criterion`)
- `locator = intake://task_spec`: provenance interna al task intake
- `source_kind = intake_provided`: non è una tool observation
- `support_level = local_only`: può sostenere trasformazioni e drafting locali, ma non equivale a una fonte esterna

---

## Passo 12: le evidenze iniziali del caso di studio

Nel caso reale, il bootstrap materializza evidenze come queste.

### Esempi dal `goal` e dal `context`

- `intake:1` -> `Draft an external email to the Acme Cloud operations team about an ongoing sensitive EU runtime incident affecting the customer notification pipeline, requesting specific manual checks and a status update within 60 minutes`
- `intake:2` -> `Incident detected 09:12 CET on May 25, 2026`
- `intake:3` -> `intermittent delivery failures for transactional notifications`
- `intake:4` -> `still under investigation`
- `intake:5` -> `no confirmed data loss`
- `intake:6` -> `delayed processing observed for some EU tenants`

### Esempi dai `constraints`

- `intake:8` -> `Email addressed externally to the operations team at Acme Cloud`
- `intake:9` -> `Describe the incident timeline and observed impact accurately as stated`
- `intake:10` -> `Do not claim confirmed data loss`
- `intake:11` -> `Request manual verification of delivery queues and check retry backlog`
- `intake:12` -> `Ask to confirm whether customer-facing impact extended beyond the notification pipeline`
- `intake:13` -> `Request status report back to the incident lead within 60 minutes`

### Esempi dai `success_criteria`

- `intake:15` -> `Email includes incident detection time/date, symptom description, and current investigation status`
- `intake:16` -> `Explicitly states no confirmed data loss while noting delayed processing for some EU tenants`
- `intake:17` -> `Lists the required operational checks (delivery queues, retry backlog) and the impact-scope confirmation question`
- `intake:18` -> `Requests a status update to the incident lead within 60 minutes`
- `intake:19` -> `Uses wording appropriate for a sensitive runtime incident and avoids unverified conclusions`

### Cosa è importante capire

Queste evidenze non sono un log decorativo. Sono il grounding iniziale del runtime.

Più avanti:

- il planner pianifica sopra questi fatti;
- l'actor li riceve nel suo `ActorTask`;
- l'oversight li usa per valutare se il supporto evidenziale è sufficiente;
- il trace finale li esporta come evidenza osservata.

---

## Passo 13: il bootstrap registra anche il primo step di runtime history

Il nodo `bootstrap_from_intake()` in `src/layer2_runtime_governance/nodes.py` aggiunge subito uno `StepRecord`.

Nel trace appare così:

```json
{
  "step_index": 0,
  "runtime_state": "planning",
  "node_name": "bootstrap_from_intake",
  "summary": "Runtime state initialized from layer 1 intake payload."
}
```

### Perché conta

Il sistema non vuole solo sapere il risultato finale. Vuole sapere in che ordine è arrivato a quel risultato.

---

## Passo 14: il planner riceve il `TaskState`, non il task grezzo

Dopo il bootstrap, il grafo passa al nodo `planner`.

Il planner lavora su:

- `task_spec`
- `execution_policy`
- `runtime_flags`
- `observed_evidence`
- `external_communication`

Non riceve più il raw input iniziale.

### Cosa produce nel caso reale

Nel run finale del caso studio, il piano risultante è di 3 step:

```json
[
  {
    "step_id": "step_1",
    "objective": "Define the email’s factual core and boundaries based strictly on the provided incident context and constraints.",
    "step_kind": "local_transformation",
    "evidence_requirement": "local_only",
    "claim_precision_ceiling": "local_only"
  },
  {
    "step_id": "step_2",
    "objective": "Select and incorporate the required operational actions and confirmations to request from the Acme Cloud operations team.",
    "step_kind": "high_level_summary",
    "evidence_requirement": "local_only",
    "claim_precision_ceiling": "local_only"
  },
  {
    "step_id": "step_3",
    "objective": "Draft the external email with an appropriate structure, urgency, and a 60-minute response deadline, keeping the claims carefully qualified and review-ready.",
    "step_kind": "final_synthesis",
    "evidence_requirement": "local_only",
    "claim_precision_ceiling": "local_only"
  }
]
```

### Cosa succede qui

Questo è un altro punto architetturale importante:

- il piano non è libero e implicito;
- ogni step ha tipo, requisito di evidenza e tetto di precisione;
- il planner produce semantica, non chiamate tool;
- la granularità del piano può essere normalizzata dal runtime.

### Normalizzazione del piano

Nel caso `external_communication`, il runtime può compattare piani troppo decomposti tramite `_compact_external_communication_plan()` in `src/layer2_runtime_governance/nodes.py`.

Nel nostro caso, il risultato finale è un piano più compatto e proporzionato:

- prima si definiscono fatti e confini;
- poi si definiscono le richieste operative;
- infine si produce direttamente il draft review-ready.

---

## Passo 15: l'actor riceve un `ActorTask` già grounded

Dopo il planner, il nodo `actor` non improvvisa. Riceve un `ActorTask` costruito da `build_actor_task()` in `src/layer2_runtime_governance/actor_contract.py`.

L'`ActorTask` contiene:

- `task_spec`
- `execution_policy`
- `runtime_flags`
- `plan_steps`
- `past_steps`
- `observed_evidence`
- `scratchpad`
- `step_validations`
- `current_plan_step`
- `required_evidence_support_level`
- `max_supported_claim_precision`

### Cosa significa davvero

L'actor non decide solo "cosa scrivere". Riceve già:

- il piano attuale;
- le evidenze già osservate;
- il livello minimo di supporto richiesto;
- il massimo livello di precisione ammesso.

Questo è il motivo per cui il sistema riesce a contenere l'overclaiming.

---

## Passo 16: esecuzione del primo step actor

### Step 1

Obiettivo:

```text
Define the email’s factual core and boundaries based strictly on the provided incident context and constraints.
```

### Cosa fa l'actor

L'actor usa esclusivamente le evidenze `local_only` provenienti dall'intake per estrarre:

- detection time;
- sintomo;
- stato dell'indagine;
- assenza di data loss confermato;
- delayed processing per alcuni tenant.

### Output prodotto

Nel trace, lo step viene registrato come completato con un output locale, non con una tool call.

### Cosa succede nel runtime

- `actor_result.result_type = intermediate_output`
- c'è una `StepValidation`
- la validazione dice: `objective_satisfied = true`, `evidence_sufficient = true`, `next_action = complete_step`
- quindi `nodes._record_actor_execution()` marca lo step come `completed`
- l'output viene copiato in `executed_steps`
- il cursore passa allo step successivo

### Stato dopo governance

La governance produce `continue`.

Quindi il flow fa:

```text
actor -> governance -> actor
```

senza fermarsi.

---

## Passo 17: esecuzione del secondo step actor

### Step 2

Obiettivo:

```text
Select and incorporate the required operational actions and confirmations to request from the Acme Cloud operations team.
```

### Cosa fa l'actor

Ancora una volta usa solo evidenze intake:

- request manual verification of delivery queues
- check retry backlog
- confirm whether customer-facing impact extended beyond the notification pipeline

### Cosa non succede

Non parte alcuna tool call perché:

- le richieste operative sono già esplicitate nell'intake;
- non serve cercare altre fonti;
- il requisito di evidenza dello step è `local_only`.

### Stato dopo governance

Anche qui la governance produce `continue`.

---

## Passo 18: esecuzione del terzo step actor

### Step 3

Obiettivo:

```text
Draft the external email with an appropriate structure, urgency, and a 60-minute response deadline, keeping the claims carefully qualified and review-ready.
```

### Cosa fa l'actor

Compone la bozza completa dell'email esterna, per esempio:

```text
Subject: Urgent: EU Notification Pipeline Incident – Manual Checks Required (Response Needed Within 60 min)

Hi Acme Cloud Operations Team,

We are currently investigating a sensitive incident affecting the EU customer-notification pipeline.

Incident summary
- Detection time: 09:12 CET on 25 May 2026
- Observed impact: intermittent delivery failures for transactional notifications and delayed processing for some EU tenants.
- Current status: investigation ongoing; no confirmed data loss at this time.

Requested actions
1. Perform a manual verification of the delivery queues for the EU notification pipeline.
2. Check the retry backlog to identify any pending or failed notification retries.
3. Confirm whether the customer-facing impact extends beyond the notification pipeline to any other services or components.

Please provide a status update on these items to the incident lead no later than 60 minutes from receipt of this email.
```

### Cosa succede nel subflow `external_communication`

Durante `_record_actor_execution()`, il runtime aggiorna lo stato `external_communication`:

- salva il body nel draft governato;
- marca il draft come `review_ready`;
- imposta la fase a `draft_proposal`.

### Punto chiave

Il runtime non considera questa bozza "già inviata" o "pronta a uscire".

La considera:

- un draft interno;
- governato;
- tracciato;
- review-ready;
- ancora dentro il boundary del runtime.

---

## Passo 19: perché `final_output_text` viene valorizzato anche se l'actor è `intermediate_output`

Questo è un dettaglio architetturale importante.

Nel caso di studio l'actor non restituisce `final_output`, ma `intermediate_output`. Eppure il runtime valorizza comunque `final_output_text`.

### Perché

Perché la regola è:

- se l'ultimo step semantico è completato;
- e la `StepValidation` dice che l'obiettivo è soddisfatto e l'evidenza è sufficiente;
- allora il runtime promuove quell'output a `final_output_text`.

Per `external_communication`, il testo finale canonico viene preso dal draft governato, non da una stringa arbitraria fuori stato.

Questo garantisce coerenza fra:

- `executed_steps`
- `external_communication.draft`
- `human_review.external_communication_draft`
- `final_output_text`

---

## Passo 20: governance del terzo step e apertura della human review

Dopo il terzo step, entra di nuovo `governance`.

### Cosa valuta la governance

La governance combina:

1. policy statica
2. policy dinamica
3. assessment comportamentale
4. judge, se necessario
5. combinatore finale

### Risultato nel caso reale

La decisione finale è:

```json
{
  "directive": "wait_human_review",
  "policy_decision": "require_human_review",
  "reason_codes": [
    "external_communication_review_required"
  ]
}
```

### Cosa significa

Non significa che il draft sia sbagliato.

Significa che, per policy architetturale:

- una comunicazione esterna review-ready;
- con impatto potenzialmente organizzativo;
- non può proseguire oltre senza un passaggio umano typed.

Questo è un cardine del progetto.

---

## Passo 21: nasce la `HumanReviewRequest`

Quando la governance mette il runtime in `waiting_human_review`, il sistema costruisce una `HumanReviewRequest` tramite `build_human_review_request()` in `src/layer2_runtime_governance/hitl.py`.

Nel caso di studio, la richiesta contiene concettualmente:

```json
{
  "current_state": "waiting_human_review",
  "resume_state": "completed",
  "current_plan_step_id": "step_3",
  "governance_directive": "wait_human_review",
  "external_communication_target_type": "external_communication",
  "allowed_decisions": [
    "approve_continue",
    "approve_continue_read_only",
    "modify_and_continue",
    "request_more_evidence",
    "replan",
    "ask_clarification",
    "abort"
  ],
  "external_communication_draft": {
    "channel": "external_communication",
    "body": "Subject: Urgent: EU Notification Pipeline Incident – Manual Checks Required ..."
  }
}
```

### Cosa succede qui

Il sistema non salva solo "serve review". Salva una richiesta typed completa che dice:

- dove il runtime si è fermato;
- a quale step si riferisce la review;
- quale draft va revisionato;
- quali decisioni sono ammesse;
- in quale stato riprenderà il graph se arriva un'approvazione.

Nel nostro caso:

- `resume_state = completed`

perché, se il reviewer approva, non esistono altri step di piano da eseguire.

---

## Passo 22: step history finale del run

Il caso di studio produce questa sequenza logica di step runtime:

```json
[
  {
    "step_index": 0,
    "runtime_state": "planning",
    "node_name": "bootstrap_from_intake"
  },
  {
    "step_index": 1,
    "runtime_state": "planning",
    "node_name": "planner"
  },
  {
    "step_index": 2,
    "runtime_state": "ready_next_step",
    "node_name": "actor"
  },
  {
    "step_index": 3,
    "runtime_state": "ready_next_step",
    "node_name": "governance"
  },
  {
    "step_index": 4,
    "runtime_state": "ready_next_step",
    "node_name": "actor"
  },
  {
    "step_index": 5,
    "runtime_state": "ready_next_step",
    "node_name": "governance"
  },
  {
    "step_index": 6,
    "runtime_state": "completed",
    "node_name": "actor"
  },
  {
    "step_index": 7,
    "runtime_state": "waiting_human_review",
    "node_name": "governance"
  }
]
```

### Lettura corretta di questa sequenza

- bootstrap inizializza;
- planner crea il piano;
- actor esegue step 1;
- governance conferma;
- actor esegue step 2;
- governance conferma;
- actor esegue step 3 e completa semanticamente il lavoro;
- governance intercetta l'external draft e apre il gate umano.

---

## Passo 23: rami non percorsi in questo esempio

Questo run è molto utile anche per capire cosa non è successo.

### Tool Gateway non percorso

Il ramo:

```text
actor -> tool_gateway -> observe_tool_result -> actor
```

non viene preso perché l'actor non emette `propose_action`.

### Perché non emette `propose_action`

Perché:

- i fatti necessari sono già presenti in intake;
- il requisito di evidenza è `local_only`;
- il tipo di task è drafting locale, non ricerca fattuale esterna;
- il draft può essere costruito senza capability esterne.

### Replanner non percorso

Il ramo:

```text
actor -> governance -> replanner -> actor
```

non viene preso perché l'actor non restituisce `needs_replan`.

### Judge non percorso

Nel run finale del caso di studio, il judge non viene invocato perché:

- la traiettoria è pulita;
- il supporto evidenziale è coerente col requisito dello step;
- non emerge una mismatch semantica che richieda escalation selettiva.

---

## Passo 24: cosa vedrebbe chi riprende il thread dopo la review

Se un reviewer sceglie `approve_continue`, il runtime:

- riprende dal checkpoint dello stesso thread;
- applica la decisione typed;
- vede che `resume_state = completed`;
- chiude il run senza altri step.

Se invece sceglie:

- `modify_and_continue`: il draft editato diventa la nuova versione governata;
- `request_more_evidence`: il runtime torna in recovery e richiede un ulteriore giro;
- `replan`: il piano attuale viene sostituito;
- `ask_clarification`: il thread resta in percorso umano;
- `abort`: il runtime termina in stato bloccato/abortito.

---

## Riassunto finale del caso di studio

### Sequenza completa in una frase

Una task grezza di email esterna viene trasformata da Layer 1 in un `IntakeResult` canonico; il bootstrap di Layer 2 la converte in `TaskState`, materializza i fatti del task come `observed_evidence` `local_only`, il planner costruisce un piano semanticamente tipizzato, l'actor usa solo quelle evidenze per completare tre step senza tool, la governance conferma i primi due passi e poi blocca il terzo draft su un gate typed di human review.

### Punto architetturale più importante

Il progetto non tratta il task intake come semplice testo descrittivo. Lo trasforma subito in:

- stato canonico;
- evidenza esplicita;
- vincoli runtime;
- policy operativa;
- confine governato per il draft esterno.

In altre parole: l'intake non è solo l'ingresso del sistema. È il primo strato di grounding e controllo.

---

## Checklist mentale per chi prende in mano il progetto

Se devi leggere un run di questa architettura, l'ordine giusto è:

1. leggi il `TaskSpec` di Layer 1;
2. verifica la `ExecutionPolicy`;
3. guarda `parse_status` e `runtime_flags`;
4. controlla quali `observed_evidence` sono nate già in bootstrap;
5. guarda i `plan_steps` e i loro `evidence_requirement`;
6. verifica cosa riceve l'actor;
7. controlla `step_validations`;
8. controlla se ci sono `tool_results_history` oppure no;
9. guarda la `governance_decision` di ogni turno;
10. se il dominio è `external_communication`, controlla sempre `external_communication` e `pending_human_review_request`.

Questa è la sequenza corretta per capire davvero il comportamento del runtime.

