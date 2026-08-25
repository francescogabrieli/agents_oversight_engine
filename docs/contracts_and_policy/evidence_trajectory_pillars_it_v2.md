# Evidence e Trajectory pillar — v2 (contratto corrente)

> **Stato del documento:** questo documento descrive il comportamento corrente del runtime e della policy OPA. La v1 (`evidence_trajectory_pillars_it.md`) è mantenuta esclusivamente come riferimento storico.

Le correzioni descritte qui restano additive dentro il contratto chiuso `layer2_trace.v6`: retrieval kind, floor/permesso di insufficienza, nuovi error code e `unresolvable_revision` sono campi espliciti nello schema. Un cambiamento incompatibile futuro dovrà introdurre una nuova versione; il recupero di checkpoint legacy può normalizzare dati mancanti, ma il Layer 5 non migra silenziosamente artefatti v6 diversi.

## Idea generale

I pillar **Evidence** e **Trajectory** forniscono a OPA fatti osservabili sulla run:

- **Evidence** controlla che le affermazioni fattuali in uscita siano *verificabili*: che il supporto dichiarato esista, sia integro e sia sufficiente rispetto a quanto lo step richiedeva. Non-circolarità e ancoraggio letterale sono applicati; la freshness segue il flag di enforcement della configurazione OPA e i reason code non applicabili vengono filtrati da Rego.
- **Trajectory** controlla che la run *termini*: che l'agente avanzi, che non ripeta replan o proposte esterne senza una nuova base osservabile, e che non superi i limiti di convergenza configurati.

Nessuno dei due valuta il significato. Evidence risponde a «il supporto c'è ed è controllabile?», Trajectory a «stiamo convergendo?». La domanda «ha senso ed è appropriato?» è del **Judge**.

I pillar non prendono autonomamente la decisione finale e non calcolano uno score in Python. Producono fatti e reason code; **OPA è l'unica autorità che restituisce la direttiva finale**.

## Quando si attivano

| Evento dell'Actor | Evidence | Trajectory |
|---|---:|---:|
| `INTERMEDIATE_OUTPUT` | No | Sì — solo liveness |
| `PROPOSE_ACTION` senza contenuto fattuale | No | Sì — solo liveness |
| `PROPOSE_ACTION` con contenuto fattuale | **Sì** | Sì — solo liveness |
| `NEEDS_REPLAN` | No | Sì — liveness + convergenza |
| `STEP_COMPLETED` locale | Sì | Sì — liveness + convergenza |
| Candidato a operazione esterna | Sì | Sì — liveness + convergenza |

Due cambi rispetto alla v1:

- **Evidence si attiva anche su `PROPOSE_ACTION` quando l'azione trasporta contenuto fattuale** (corpo di un'email, statement di database). Nella v1 era escluso perché read-only: è la lacuna per cui un candidato esterno può raggiungere il Judge con zero riferimenti dichiarati.
- **Trajectory è sempre attivo**, con due modalità. In fase esplorativa osserva solo la liveness (ritenti, fallimenti tool consecutivi, nuove basi acquisite). In fase di impegno aggiunge le regole di convergenza. Nella v1 la fase esplorativa non era osservata da nessun pillar.

## Evidence pillar

### L'onere probatorio dichiarato

Evidence può decidere solo se sa **cosa** doveva essere dimostrato. Il criterio non è cablato nel pillar: è dichiarato dal planner in ogni step che produce affermazioni fattuali.

```python
PlanStep(
    step_id="step_4",
    objective="Inviare l'email di sintesi con le fonti raccolte.",
    produces_factual_claims=True,
    evidence_burden=EvidenceBurden(
        min_independent_sources=3,
        require_retrieved_content=True,
        freshness_window_days=30,
        insufficiency_declaration_permitted=True,
    ),
    produces_governed_external_effect_proposal=True,
)
```

Se `produces_factual_claims` è falso, Trajectory resta attivo per la liveness ma Evidence non ha un onere di copertura, salvo il caso di uno step marcato `produces_governed_external_effect_proposal`: quel marker attiva comunque le cinque famiglie strutturali, anche se il burden dichiarato è assente. Se lo step è fattuale, `evidence_burden` è obbligatorio al confine canonico: il costruttore di `PlanStep`, gli schema del planner/replanner e il preflight runtime rifiutano lo step malformato. In aggiunta, il runtime deriva dal `TaskSpec` un floor esplicito quando `constraints` o `success_criteria` chiedono un numero minimo di fonti o una freshness window; un burden planner più debole viene rialzato e il replanner non può abbassarlo. Un eventuale stato legacy che abbia oltrepassato quel confine espone `missing_evidence_burden` nei fatti diagnostici, senza leggere soglie OPA da Python.

`insufficiency_declaration_permitted` non è un bypass generico: può essere rialzato dal floor del task solo quando il testo del task autorizza esplicitamente una dichiarazione di insufficienza. Se la copertura non raggiunge il minimo, la dichiarazione presente nel payload costituisce l'alternativa ammessa; senza dichiarazione il gap resta attivo. Il riconoscimento è per frase e indipendente dall'ordine delle parole: la frase deve nominare le fonti e asserirne la carenza, in qualunque sequenza. Una clausola condizionale che si limita a riportare la regola del task («se le fonti sono meno di tre, dichiaralo») non è una dichiarazione e viene esclusa. La verifica finale di questa alternativa appartiene a Rego, che usa i fatti prodotti dal signal service.

Il contratto distingue esplicitamente `completion_attempt` da `factual_completion_attempt`: il primo è vero per ogni `STEP_COMPLETED`, il secondo solo quando quello stesso step dichiara `produces_factual_claims=true`. Per il candidato di effetto esterno si usa anche `evidence_completion_attempt`, che attiva lo stesso controllo strutturale quando `produces_governed_external_effect_proposal=true`. L'integrità dei riferimenti e il binding dei digest restano controllati a ogni completamento; burden, fragilità dei soli titoli, non-circolarità e ancoraggio letterale diventano deficit attivi al confine fattuale o di effetto esterno. Numeri o date calcolati esclusivamente dai vincoli forniti dall'utente — per esempio la finestra di 30 giorni rispetto a una data di riferimento già data — restano osservabili nel trace, ma non generano da soli `REQUEST_MORE_EVIDENCE`.

Il replan non può azzerare questo contratto. Il prompt riceve il `remaining_evidence_obligation` aggregato sulla traiettoria corrente e pendente; il runtime rifiuta una sostituzione che elimini il lavoro fattuale o indebolisca numero minimo di fonti, requisito di contenuto recuperato o finestra di freshness. Il replanner di produzione dispone di un solo tentativo correttivo tipizzato, poi fallisce chiuso; il fallback locale conserva invece gli step futuri originali.

Il marker `produces_governed_external_effect_proposal` è un contratto distinto dal burden: non misura evidenza, ma garantisce che un task `execute_after_governance` possieda nel piano esattamente uno step terminale che produrrà la proposta operativa tipizzata. Se il planner restituisce soltanto uno step preparatorio, riceve un unico feedback correttivo prima dell'avvio dell'actor; se fallisce ancora, il runtime si arresta invece di trasformare l'omissione in un replan della trajectory.

### Come viene registrata un'evidenza

Invariato rispetto alla v1. Quando un tool restituisce un risultato, il runtime crea un'osservazione concreta:

```python
ToolObservation(
    observation_id="obs_17",
    content="Il documento indica che il termine è il 30 settembre.",
    locator="https://example.org/documento",
    retrieval_kind=RetrievalKind.FETCHED_DOCUMENT,
)
```

L'osservazione è conservata nel `TaskState` con la sua provenienza: tool call e plan step che l'hanno generata. `retrieval_kind` è assegnato dall'adapter (`search_snippet`, `fetched_document`, `controlled_resource`, `mcp_result` o `capability_result`) e impedisce di dedurre la natura dell'evidenza da prefissi testuali. Uno `search_snippet` può guidare una fetch, ma non soddisfa `require_retrieved_content` né incrementa il conteggio delle origini indipendenti. Quando completa uno step, l'Actor dichiara quali osservazioni ha usato:

Il Tool Bus tratta gli esiti HTTP in modo esplicito: `404` e `410` sono `target_not_found` terminali, `403` e gli altri `4xx` non transitori sono `forbidden`, mentre timeout, `429` e `5xx` sono gli unici esiti HTTP retryable. Un body che contiene solo challenge/interstitial viene marcato `bot_challenge` ed è `extractability_limited=true`, quindi non diventa evidenza. Una `fetch_url_text` è ammessa solo quando l'URL coincide canonicamente con un locator già osservato o con un URL esplicito nel `TaskSpec`; altrimenti il runtime produce `unanchored_target` senza invocare il backend e senza incrementare i fallimenti del backend.

Per documenti HTML e XHTML, `source_timestamp` rappresenta una data di pubblicazione semanticamente attribuita: metadata editoriali, JSON-LD `datePublished`, elemento data marcato come pubblicazione oppure nodo metadata-like il cui contenuto completo coincide con `Published <date>`. Frasi generiche nel body e label di update/revisione non costituiscono prova della data della pagina. L'header HTTP `Last-Modified` resta metadata di trasporto e non soddisfa mai la freshness editoriale, neppure per testo o PDF; quei formati richiederanno un estrattore semantico dedicato. Nel controllo di ancoraggio, URL del candidato e locator osservati sono confrontati come token esatti, mai per sottostringa; il locator selezionato e il timestamp attribuito fanno parte del corpus. Un punto o una virgola terminale sono ignorati soltanto se la variante ripulita coincide esattamente con un locator osservato; la punteggiatura significativa per l'URL resta parte del token.

```python
StepValidation(
    actor_self_confidence=0.9,
    evidence_refs=("obs_17",),
    rationale_summary="La data è riportata in obs_17.",
)
```

### Cosa controlla

I controlli producono fatti grezzi e `deficit_codes`; OPA li combina con lo stato della run e restituisce la direttiva finale. Le categorie operative sono integrità referenziale, copertura dell'onere dichiarato o derivato dal `TaskSpec`, disponibilità del contenuto recuperato, fragilità dei titoli e ancoraggio letterale. Le cinque famiglie si valutano quando il completamento è fattuale oppure quando il passo prepara una proposta di effetto esterno governato.

**1. Integrità referenziale.** Ogni `evidence_ref` esiste in `TaskState.observations`; riferimenti e ID sono univoci; le osservazioni selezionate contengono testo concreto; la provenienza risale a una tool call e a uno step conosciuti; snapshot, selezione e operazione coincidono con i digest sigillati alla creazione del candidato.

```text
selected_evidence_refs_valid
selected_evidence_refs_unique
selected_observations_have_content
selected_observations_provenance_resolvable
candidate_digest_binding_valid
```

**2. Copertura dell'onere dichiarato o derivato.** Aritmetica su tipi, non semantica. L'onere appartiene al *claim*, quindi il conteggio usa i **riferimenti selezionati** dall'Actor: aver recuperato tre fonti nella run non copre un'affermazione che ne cita una. `retrieved_independent_sources_count` resta esportato come diagnostico sull'universo recuperato e non decide nulla; serve a distinguere «le fonti non sono state trovate» da «le fonti non sono state citate», che richiedono remediation opposte. Il gate pre-turno dell'Actor può osservare solo l'universo, perché la selezione non esiste ancora quando il turno viene costruito: è quindi **strettamente più debole** di questo controllo — un universo sotto il minimo implica una selezione sotto il minimo — e non può mai aprire un completamento che Evidence rifiuterebbe per copertura. L'invariante è fissata da un test in `tests/test_layer2_execution.py`.

```text
distinct_independent_sources_count = 3
min_independent_sources_required = 3
burden_satisfied = true
```

L'indipendenza è strutturale: locator distinti per schema e dominio. Non è un giudizio sull'indipendenza editoriale delle fonti, che resta del Judge.

**3. Qualità strutturale dell'osservazione e freshness.** Evidence distingue la data attribuita alla fonte dal momento di retrieval. Quando il burden dichiara `freshness_window_days`, il signal service verifica che le osservazioni selezionate abbiano una data valida e compresa nella finestra. Nella configurazione corrente la freshness resta visibile nel trace ma non apre da sola un evidence gap. Il fetch HTTP usa date editoriali di pubblicazione estratte dal documento; `Last-Modified`, momento di retrieval e semplici date presenti nel testo non sono usati come surrogati.

```text
selected_evidence_all_title_only
retrieved_content_source_count
freshness_satisfied
```

Senza una finestra dichiarata, `freshness_evaluated=false`: il controllo temporale non viene inferito né simulato.

**4. Non-circolarità.** Il controllo è attivo ma deliberatamente strutturale e limitato: rifiuta un'osservazione selezionata se proviene da un tool di trasformazione actor-derived (`mcp.local_test.echo` o `mcp.local_test.text_stats`) oppure se il locator tipizzato indica un artefatto auto-prodotto (`actor-output://`, `agent-output://`, `draft://`, `runtime-artifact://`, `scratchpad://`, `workspace://`). In questi casi `circular_reference_detected=true`, il deficit è `circular_evidence_reference` e OPA richiede altra evidenza.

```text
circular_reference_detected = true
circular_evidence_reasons = {"obs_21": "self_produced_locator"}
```

Il controllo non pretende di riconoscere una circolarità semantica o una parafrasi di un output dell'Actor: quei casi restano responsabilità del Judge o di un futuro detector con una nuova provenienza tipizzata.

**5. Ancoraggio letterale.** Sui soli token estraibili — numeri, date, importi, URL, indirizzi. Un letterale presente nel payload deve comparire nel testo del `TaskSpec` oppure in almeno un'osservazione o nella sua provenienza tipizzata; il testo d'intake non viene materializzato come `ToolObservation`. La sola punteggiatura di frase dopo un URL viene normalizzata. Una data che il task enuncia in prosa (`24 agosto 2026`) ancora ogni resa numerica equivalente (`2026-08-24`, `24/08/2026`, `2026/08/24`, `24-08-2026`): è lo stesso valore autoritativo, e la notazione scelta dall'Actor non è un claim da provare. Le date di pubblicazione delle fonti restano invece ancorabili solo dalle osservazioni. Per gli URL, schema/host, porta di default, slash terminale e parametri di tracking (`utm_*`, `gclid`, `fbclid` e analoghi) sono normalizzati, mentre path e query semanticamente significativi restano distinti. Le numerazioni strutturali dichiarate (`Articolo 1.1`, `Step 2.1`) e i frammenti numerici interni a identificatori non sono claim; il separatore decimale locale degli importi è normalizzato senza cambiare il valore. Per un completamento fattuale o una proposta esterna, Rego consuma ora entrambi i fatti di ancoraggio: anche uno solo non vuoto apre `evidence_gap`.

```text
unanchored_literals_in_selection = ["18%"]
unanchored_literals_in_universe  = ["18%"]
```

I due fatti restano distinti per diagnosi e feedback. Assente dalla selezione ma presente altrove nell'universo è un difetto di attribuzione; assente da **ogni** osservazione è una cifra non supportata. Entrambi producono `REQUEST_MORE_EVIDENCE` finché il tetto non trasferisce il caso a revisione umana.

### Cosa decide

I fatti di Evidence non producono direttamente una revisione semantica del payload: OPA li usa nel percorso di ammissione.

| Situazione | Fatti / condizione | Direttiva OPA | Reason code |
|---|---|---|---|
| **Burden non dichiarato** | step fattuale senza `evidence_burden` e senza floor applicabile | nessuna direttiva da solo | `missing_evidence_burden` nel risultato diagnostico di Evidence |
| **Insufficienza esplicita ammessa** | copertura sotto il minimo, `insufficiency_declaration_permitted=true` e dichiarazione presente nel payload | nessuna direttiva da sola | fatto alternativo consumato da Rego |
| **Gap strutturale o di copertura** | integrità non valida, contenuto recuperato mancante, non-circolarità non soddisfatta, onere applicabile non soddisfatto o letterali non ancorati | `REQUEST_MORE_EVIDENCE` | `invalid_or_missing_selected_evidence` |
| **Freshness non soddisfatta (configurazione corrente)** | data mancante, obsoleta o futura con `freshness_enforcement_enabled=false` | nessuna direttiva da sola; diagnostica preservata | reason code specifico di freshness |
| **Tetto delle richieste raggiunto** | il risultato OPA `evidence_request_ceiling_reached=true`, prodotto dal conteggio configurato | `WAIT_HUMAN_REVIEW` | `evidence_request_limit_reached` |
| **Pillar richiesto ma non disponibile** | errore del signal service | `WAIT_HUMAN_REVIEW` | `required_pillar_unavailable` |

`REVISE_ACTION` resta invece una direttiva del percorso Judge quando la valutazione semantica richiede una modifica del candidato.

Una selezione vuota è un gap di copertura quando esiste un burden dichiarato che la rende insufficiente. Nel solo percorso difensivo in cui uno stato privo di burden raggiunge comunque OPA, `coverage_evaluated=false`: l'assenza della dichiarazione rimane visibile, ma non autorizza da sola `REQUEST_MORE_EVIDENCE` o HITL.

### Il ciclo di `REQUEST_MORE_EVIDENCE`

Nella v1 questa direttiva ripristinava lo step e non lasciava traccia: nessun feedback all'Actor, nessun contatore, nessun tetto. Nella v2 è simmetrica al ciclo di revisione del Judge:

1. il runtime scrive un `GovernanceActionFeedback` che l'Actor riceve nel turno successivo, con il difetto esatto e gli ID interessati — per esempio «queste osservazioni sono prive di timestamp: apri i documenti specifici» oppure «questa osservazione è fuori finestra: rimuovila o sostituiscila»;
2. `evidence_request_count` conserva il volume whole-run per audit, mentre `evidence_request_episode_count` conta i tentativi relativi allo step o candidato corrente. Il signal service non replica la soglia: persiste il booleano validato dal risultato OPA `evidence_request_ceiling_reached`, che viene passato al turno dell'Actor;
3. un nuovo step apre automaticamente un nuovo episodio; una decisione umana `request_more_evidence` o `replan` apre esplicitamente un nuovo episodio senza cancellare il totale whole-run;
4. raggiunto il tetto, la direttiva diventa `WAIT_HUMAN_REVIEW`. Questo review espone soltanto `request_more_evidence`, `replan` e `abort`: un generico `approve_continue` non è un'eccezione digest-bound all'onere di evidenza e quindi non può bypassarlo.

Senza questi quattro elementi il pillar può bloccare senza poter terminare: è la configurazione che destabilizza davvero la run.

### Esempio in cui Evidence passa

Step `step_4`, onere dichiarato: 3 fonti indipendenti, contenuto recuperato, finestra 30 giorni. L'Actor propone il candidato email dichiarando 4 riferimenti.

```text
obs_07  https://a.example/report   contenuto pieno   12 giorni
obs_11  https://b.example/news     contenuto pieno    5 giorni
obs_14  https://c.example/paper    contenuto pieno   21 giorni
obs_19  search:...                 solo titolo         —
```

Il corpo afferma: «Tre fonti indipendenti confermano un aumento del 12% nel Q2 2026.»

```text
selected_evidence_refs_valid          = true
candidate_digest_binding_valid        = true
distinct_independent_sources_count    = 3
burden_satisfied                      = true
freshness_satisfied                   = true
circular_reference_detected           = false
unanchored_literals_in_universe       = []
evidence_gap                          = false
```

`burden_satisfied = true` indica che la copertura strutturale è sufficiente. `obs_19` è solo un titolo ma non serve al conteggio, quindi non guasta nulla. Il candidato passa al Judge, che ora valuta se il 12% è davvero quello che dice il report e se le fonti sono indipendenti nel merito — su una base garantita non vuota e citabile.

### Esempi in cui Evidence blocca

**A — onere non soddisfatto.** Selezione vuota, universo di 23 osservazioni, il corpo dichiara «Fonti effettivamente disponibili entro la finestra: 2».

```text
distinct_independent_sources_count = 0
burden_satisfied                 = false
evidence_gap                  = true
```

Deficit code `insufficient_independent_sources`; OPA restituisce `REQUEST_MORE_EVIDENCE` con reason code `invalid_or_missing_selected_evidence`, feedback e contatore a 1. Nella v1 questo caso passa: tutti i controlli sono vacuamente veri su selezione vuota.

**B — copertura solo apparente.** Tre riferimenti, tutti solo titoli di ricerca.

```text
distinct_independent_sources_count = 0
selected_evidence_all_title_only   = true
burden_satisfied                   = false
```

Deficit codes `insufficient_independent_sources` e `title_only_evidence_fragility`, distinti dal caso A. OPA usa comunque il reason code di direttiva `invalid_or_missing_selected_evidence`; dal trace si distinguono i deficit grezzi che hanno prodotto il gap.

**C — letterale non ancorato.** Onere soddisfatto, tre fonti piene e fresche. Ma il corpo dice «un aumento del 18%» e `"18%"` non compare in nessuna delle 23 osservazioni.

```text
burden_satisfied                 = true
unanchored_literals_in_universe  = ["18%"]
evidence_gap                     = true
```

Deficit code `unanchored_literals_in_universe`; OPA restituisce `REQUEST_MORE_EVIDENCE` con reason code `invalid_or_missing_selected_evidence`.

**Caso da non bloccare, per contrasto.** Il corpo dice «circa un quinto» dove le osservazioni dicono «19,4%». «Un quinto» non è un token estraibile, l'ancoraggio non si pronuncia, la parafrasi va al Judge. Evidence non deve mai dover decidere se «circa un quinto» equivale a 19,4%.

### Limite del controllo

Invariato nella sostanza, più preciso nella formulazione. Evidence accerta la **verificabilità**, non la **veridicità**: che l'attribuzione esista, sia ben formata e sia sufficiente. Che sia corretta è entailment semantico, ed è del Judge.

Se `obs_17` dice «aumento del 3%» e l'Actor scrive «aumento del 30%», l'ancoraggio letterale intercetta il caso — `"30%"` non compare in nessuna osservazione. Se l'Actor scrive «aumento significativo», l'ancoraggio non si pronuncia e la valutazione resta al Judge.

## Trajectory pillar

Trajectory osserva la forma del percorso, non la qualità di un output. Non chiede mai una revisione di contenuto e non nega mai per merito.

### Cosa osserva

- turni dell'Actor, step completati, osservazioni raccolte;
- tool call realmente eseguite, risultati sintetici del guard e fallimenti consecutivi nell'episodio corrente;
- numero di replan totali, replan nell'episodio corrente e presenza di una motivazione;
- nuove tool call, osservazioni o step completati dall'ultimo replan;
- revisioni della stessa operazione esterna;
- ripresentazione dello stesso candidato o di un payload già respinto.
- ultimo verdetto Judge `needs_revision` senza nuove tool call, osservazioni o step completati.

Una differenza rispetto alla v1: **gli interventi umani non contano come nuova base osservabile.** Nella v1 `human_review_count` entrava nel delta di `observable_change_since_last_replan`, quindi una review umana azzerava gratuitamente il vincolo di liveness. Una decisione umana non è un fatto nuovo sul mondo. Può però chiudere esplicitamente l'episodio di controllo sottoposto a review: questa è una transizione HITL auditabile, non un'evidenza di progresso autonomo.

L'identità di una richiesta tool è costruita da capability e argomenti eseguibili normalizzati; `reason` e altra prosa dell'Actor non possono creare un candidato artificialmente nuovo. Nei batch la proposta governata viene inoltre ricalcolata dalla `current_tool_request`: quando il cursore passa dalla prima alla seconda richiesta, target e digest passano insieme e la review non può restare legata all'operazione precedente.

### Contratto di consumo dei fatti

Il contratto runtime corrente esporta 24 `raw_facts`. Undici sono consumati direttamente
da Trajectory/OPA per la convergenza: `needs_replan_signal`, `replan_reason_present`,
`first_replan_request`, `observable_change_since_last_replan`,
`external_action_revision_count`, `unchanged_resubmission`,
`repeated_rejected_payload_digest`, `replan_episode_count`, `consecutive_tool_failures`,
`action_churn_count` ed `external_candidate`. Il totale `replan_count` resta quindi
auditabile senza trasformarsi in una condizione permanente di blocco.

I restanti tredici — `actor_turn_count`, `progress_epoch`, `completed_step_count`,
`tool_call_count`, `successful_tool_call_count`, `failed_tool_call_count`,
`synthetic_tool_result_count`, `observation_count`, `replan_count`,
`consecutive_provider_protocol_failures`, `completion_signal`, `provider_recovery_active` e
`provider_protocol_retry_exhausted` — sono fatti di trace/evaluation. Alcuni contatori
sono sorgenti locali per costruire fatti derivati, come
`observable_change_since_last_replan`; non diventano per questo soglie OPA autonome.
`progress_epoch` e i marker di recovery restano inoltre sotto il controllo tecnico del
runtime. Il risultato OPA pubblica il manifest `fact_usage` per rendere questa
separazione verificabile e impedire che un nuovo campo venga aggiunto senza decidere il
suo ruolo.

### Regole di convergenza

Nella v1 esistevano solo regole di liveness e di ripresentazione. La v2 aggiunge i tetti, ciascuno con il proprio reason code — l'attribuzione per pillar nelle metriche dipende da questa granularità.

| Regola | Condizione | Direttiva |
|---|---|---|
| `replan_request_stalled` | replan chiesto senza nuova base osservabile | `WAIT_HUMAN_REVIEW` |
| `replan_ceiling_reached` | l'Actor chiede un nuovo replan dopo aver raggiunto il limite nell'episodio corrente | `WAIT_HUMAN_REVIEW` |
| `consecutive_tool_failures` | almeno 4 invocazioni backend fallite di fila nell'episodio corrente | `WAIT_HUMAN_REVIEW` |
| `action_churn` | il candidato external corrente oscilla fra varianti già viste della stessa operazione | `WAIT_HUMAN_REVIEW` |
| `unchanged_resubmission` | stesso payload e stessa selezione ripresentati | `WAIT_HUMAN_REVIEW` |
| `repeated_rejected_payload_digest` | ripresentato un digest già respinto | `WAIT_HUMAN_REVIEW` |
| `unresolvable_revision` | ultimo `needs_revision` Judge ancora legato allo stesso digest e senza nuova base osservabile | `WAIT_HUMAN_REVIEW` |
| `action_revision_limit_reached` | revisioni che raggiungono `max_action_revisions_before_review` | `WAIT_HUMAN_REVIEW` |

`replan_ceiling_reached`, `consecutive_tool_failures` e `action_churn` sono nuove. `consecutive_tool_failures` resta sempre osservabile, ma sotto la soglia di revisione non produce direttive e non forza un replan. Cache hit, duplicati respinti e circuit breaker sono risultati sintetici: alimentano `synthetic_tool_result_count`, non il conteggio dei fallimenti backend. `action_churn` è limitato all'operazione corrente, riparte dopo una modifica umana e non può bloccare un pass non-external. Le altre regole esistono già e funzionano: `unchanged_resubmission` e `repeated_rejected_payload_digest` sono la parte più solida del sistema attuale.

`unresolvable_revision` non corregge il verdetto del Judge e non crea un bypass: rende esplicito il limite non deterministico del ciclo di revisione e apre una review con `MODIFY_AND_CONTINUE`, `REPLAN` o `ABORT`, senza autorizzare l'esecuzione esterna.

Due correzioni sulle regole esistenti:

- `action_revision_limit_reached` va confrontata con `max_action_revisions_before_review` e non con quel valore meno uno: con limite configurato a 3 la v1 scatta a 2;
- quando più regole sono vere nello stesso pass, OPA deve riportare la regola decisiva **e** popolare `rejected_alternatives` con le altre. Nella v1 `rejected_alternatives` è sempre vuoto, quindi un pass in cui scattano quattro patologie diverse ne conserva una sola.

### Esempio di replan

Al primo blocco, l'Actor restituisce:

```text
NEEDS_REPLAN
reason = "La fonte richiesta non è disponibile."
```

Primo replan, motivazione presente:

```text
replan_request_admissible = true
```

OPA restituisce `REPLAN`. Se subito dopo l'Actor richiede un altro replan senza nuove tool call, osservazioni o progressi:

```text
observable_change_since_last_replan = false
replan_request_stalled              = true
```

OPA restituisce `WAIT_HUMAN_REVIEW`. Se invece la base cambia ogni volta ma i replan continuano a susseguirsi, `replan_episode_count` cresce. Il ceiling interviene soltanto davanti a una nuova richiesta `NEEDS_REPLAN` oltre la soglia: non resta vero su ogni passaggio futuro di governance. Un'approvazione legata all'esatta richiesta HITL chiude l'episodio revisionato senza autorizzare eventuali operazioni external coincidenti; `replan_count` totale rimane nel trace.

### Esempio di proposta esterna

L'Actor propone l'invio di un'email. Il Judge richiede una revisione, il runtime registra il tentativo e scrive il feedback. L'Actor revisiona: nuovo payload, nuovo digest, `external_action_revision_count = 1`. Il Judge ri-segnala lo stesso difetto sostanziale. L'Actor ripresenta il payload identico:

```text
unchanged_resubmission            = true
repeated_rejected_payload_digest  = true
action_revision_limit_reached     = true
```

OPA restituisce `WAIT_HUMAN_REVIEW` con reason code `rejected_candidate_resubmitted`, e `rejected_alternatives` conserva le altre due. Questo percorso è già osservato end-to-end nelle run di benchmark: è il ciclo di revisione limitato che chiude correttamente.

Se il Judge restituisce ancora `needs_revision` per lo stesso digest e tra i due pass non sono comparsi nuove osservazioni, tool call o step completati, Trajectory emette `unresolvable_revision`. Il codice compare anche in `rejected_alternatives` quando un'altra precedenza è decisiva, così la causa resta ricostruibile.

## Sintesi del flusso

```text
Actor emette un risultato tipizzato
                    |
        +-----------+-----------+
        |                       |
   Evidence                  Trajectory
 il supporto c'è          stiamo convergendo
 ed è controllabile?             ?
 (sui soli punti di       (sempre; convergenza
  impegno fattuale)        sui punti di impegno)
        |                       |
        +-----------+-----------+
                    |
                   OPA
                    |
        pillar deterministici superati?
                    |
                  Judge
      ha senso ed è appropriato?
                    |
                   OPA
                    |
 CONTINUE / REQUEST_MORE_EVIDENCE / REVISE_ACTION /
              REPLAN / WAIT_HUMAN_REVIEW
```

In breve: **Evidence protegge la verificabilità del legame fra affermazione e osservazione; Trajectory protegge la terminazione della run; il Judge valuta il merito su una base che i primi due garantiscono; OPA decide come il runtime deve proseguire.**

## Differenze rispetto alla v1

| Aspetto | v1 | v2 |
|---|---|---|
| Attivazione Evidence | solo `STEP_COMPLETED` e candidato esterno | anche `PROPOSE_ACTION` con contenuto fattuale |
| Attivazione Trajectory | `NEEDS_REPLAN` e `STEP_COMPLETED` | sempre, con modalità liveness e convergenza |
| Criterio di Evidence | integrità dei riferimenti scelti | integrità **più** onere probatorio dichiarato o floor esplicito del `TaskSpec` per lo step fattuale; famiglie 2–5 attive anche sul candidato esterno |
| Selezione vuota | mai invalida | invalida quando non soddisfa il burden dichiarato; ogni step fattuale canonico deve dichiararlo |
| Esito di Evidence | un booleano, un reason code | fatti grezzi e deficit codes, con mapping OPA a direttive osservabili |
| `REQUEST_MORE_EVIDENCE` | senza feedback, senza contatore, senza tetto | feedback all'Actor, contatore, escalation a revisione umana |
| Ancoraggio del contenuto | assente | letterale attivo sui token estraibili, con distinzione selezione/universo |
| Provenienza del contenuto | euristica sui prefissi | `retrieval_kind` tipizzato dall'adapter; gli snippet di ricerca non sono documenti recuperati |
| Non-circolarità | assente | controllo strutturale attivo su provenienza actor-derived e locator auto-prodotti; la circolarità semantica resta fuori scope |
| Regole di convergenza | solo liveness e ripresentazione | più tetto sui replan, fallimenti consecutivi, churn |
| Interventi umani nella liveness | contano come nuova base | non sono fatti sul mondo; chiudono solo l'episodio di controllo revisionato |
| Revisione Judge senza progresso | ripresentazione indistinta | `unresolvable_revision` distinto e non autorizzante |
| Regole concorrenti | una sola riportata | decisiva riportata, altre in `rejected_alternatives` |
| Limite di revisione | scatta a limite meno uno | `action_revision_limit_reached` scatta al limite |

### Ordine di adozione

L'ordine conta perché Evidence e Trajectory partecipano ora al percorso attivo con direttive OPA osservabili e tracciate.

1. **Trajectory always-on**: produce fatti di liveness e convergenza su tutti gli eventi dell'Actor, con vincoli decisionali ai confini di replan, completamento e proposta esterna.
2. **Evidence event-sensitive**: è richiesto per gli step fattuali con burden obbligatorio, per gli step completati e per i candidati di external operation ammessi; resta non richiesto per output intermedi e proposte non fattuali.
3. **Feedback e tetti**: `REQUEST_MORE_EVIDENCE` incrementa il contatore e si trasforma in `WAIT_HUMAN_REVIEW` al limite configurato.
4. **Judge separato**: OPA ammette il candidato solo dopo Evidence e Trajectory; il Judge valuta poi il significato e l'appropriatezza del contenuto quando è richiesto.
5. **Risk e Behavioral restano shadow-only**: forniscono segnali e conseguenze operative senza autorità sulla direttiva finale.

Il criterio di non-regressione è la sequenza di direttive e reason code delle run di benchmark. Una ripresentazione invariata viene instradata a revisione umana; la classificazione finale della terminazione dipende dalla decisione umana o dall'esito del runtime.
