# Evidence e Trajectory pillar — v2 (contratto corrente)

> **Stato del documento:** questo documento descrive il comportamento corrente del runtime e della policy OPA. La v1 (`evidence_trajectory_pillars_it.md`) è mantenuta esclusivamente come riferimento storico.

## Idea generale

I pillar **Evidence** e **Trajectory** forniscono a OPA fatti osservabili sulla run:

- **Evidence** controlla che le affermazioni fattuali in uscita siano *verificabili*: che il supporto dichiarato esista, sia integro, sia sufficiente rispetto a quanto lo step richiedeva e che il contratto esponga i controlli di non-circolarità.
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

Evidence può decidere solo se sa **cosa** doveva essere dimostrato. Il criterio non è cablato nel pillar: è dichiarato dal planner in ogni plan step.

```python
PlanStep(
    step_id="step_4",
    objective="Inviare l'email di sintesi con le fonti raccolte.",
    produces_factual_claims=True,
    evidence_burden=EvidenceBurden(
        min_independent_sources=3,
        require_retrieved_content=True,
        freshness_window_days=30,
    ),
)
```

Se `produces_factual_claims` è falso, Trajectory resta attivo per la liveness ma Evidence non è richiesto. Se lo step dichiara `produces_factual_claims=True` e un `evidence_burden`, Evidence è valutativo e la sua copertura entra nella decisione OPA. Un burden assente non abilita una verifica di copertura; non rende però “shadow” un Evidence richiesto. I controlli strutturali restano fail-closed quando il pillar è richiesto.

### Come viene registrata un'evidenza

Invariato rispetto alla v1. Quando un tool restituisce un risultato, il runtime crea un'osservazione concreta:

```python
ToolObservation(
    observation_id="obs_17",
    content="Il documento indica che il termine è il 30 settembre.",
    locator="https://example.org/documento",
)
```

L'osservazione è conservata nel `TaskState` con la sua provenienza: tool call e plan step che l'hanno generata. Quando completa uno step, l'Actor dichiara quali osservazioni ha usato:

```python
StepValidation(
    actor_self_confidence=0.9,
    evidence_refs=("obs_17",),
    rationale_summary="La data è riportata in obs_17.",
)
```

### Cosa controlla

I controlli producono fatti grezzi e `deficit_codes`; OPA li combina con lo stato della run e restituisce la direttiva finale. Le categorie operative sono integrità referenziale, copertura dell'onere dichiarato, disponibilità del contenuto recuperato, fragilità dei titoli e ancoraggio letterale.

**1. Integrità referenziale.** Ogni `evidence_ref` esiste in `TaskState.observations`; riferimenti e ID sono univoci; le osservazioni selezionate contengono testo concreto; la provenienza risale a una tool call e a uno step conosciuti; snapshot, selezione e operazione coincidono con i digest sigillati alla creazione del candidato.

```text
selected_evidence_refs_valid
selected_evidence_refs_unique
selected_observations_have_content
selected_observations_provenance_resolvable
candidate_digest_binding_valid
```

**2. Copertura dell'onere dichiarato.** Aritmetica su tipi, non semantica.

```text
distinct_independent_sources_count = 3
min_independent_sources_required = 3
burden_satisfied = true
```

L'indipendenza è strutturale: locator distinti per schema e dominio. Non è un giudizio sull'indipendenza editoriale delle fonti, che resta del Judge.

**3. Qualità strutturale dell'osservazione.** Contenuto recuperato contro solo titolo di ricerca, schema del locator, flag di troncamento e il campo di freshness rispetto alla finestra dichiarata. Nel runtime corrente `freshness_satisfied` è esposto come fatto contrattuale ma resta predisposto per l'implementazione della verifica temporale.

```text
selected_evidence_all_title_only
retrieved_content_source_count
freshness_satisfied
```

**4. Non-circolarità.** Il contratto espone `circular_reference_detected` per rilevare riferimenti a file scritti nel workspace, draft precedenti o voci di scratchpad. Nel runtime corrente il fatto è inizializzato a `false`: il rilevamento effettivo è un'estensione dichiarata, non una verifica già attiva.

```text
circular_reference_detected = false
```

**5. Ancoraggio letterale.** Sui soli token estraibili — numeri, date, importi, URL, indirizzi. Un letterale presente nel payload deve comparire verbatim in almeno un'osservazione.

```text
unanchored_literals_in_selection = ["18%"]
unanchored_literals_in_universe  = ["18%"]
```

I due fatti sono distinti perché portano a decisioni diverse. Assente dalla selezione ma presente altrove nell'universo è un difetto di attribuzione. Assente da **ogni** osservazione è una cifra inventata.

### Cosa decide

I fatti di Evidence non producono direttamente una revisione semantica del payload: OPA li usa nel percorso di ammissione.

| Situazione | Fatti / condizione | Direttiva OPA | Reason code |
|---|---|---|---|
| **Gap strutturale o di copertura** | integrità non valida, contenuto recuperato mancante, onere non soddisfatto o letterali non ancorati | `REQUEST_MORE_EVIDENCE` | `invalid_or_missing_selected_evidence` |
| **Tetto delle richieste raggiunto** | `evidence_request_count` oltre il limite configurato | `WAIT_HUMAN_REVIEW` | `evidence_request_limit_reached` |
| **Pillar richiesto ma non disponibile** | errore del signal service | `WAIT_HUMAN_REVIEW` | `required_pillar_unavailable` |

`REVISE_ACTION` resta invece una direttiva del percorso Judge quando la valutazione semantica richiede una modifica del candidato.

Nota che nella v1 una selezione vuota non era automaticamente invalida. **Nella v2 lo è, ma solo quando lo step ha dichiarato un onere probatorio.** Su uno step che non produce affermazioni fattuali la selezione vuota resta legittima.

### Il ciclo di `REQUEST_MORE_EVIDENCE`

Nella v1 questa direttiva ripristinava lo step e non lasciava traccia: nessun feedback all'Actor, nessun contatore, nessun tetto. Nella v2 è simmetrica al ciclo di revisione del Judge:

1. il runtime scrive un `GovernanceActionFeedback` che l'Actor riceve nel turno successivo, con il difetto esatto — «lo step richiede 3 fonti indipendenti con contenuto recuperato; ne hai dichiarate 0 su 23 osservazioni disponibili»;
2. `evidence_request_count` sale ed è un fatto per OPA;
3. raggiunto il tetto, la direttiva diventa `WAIT_HUMAN_REVIEW`.

Senza queste tre cose il pillar può bloccare senza poter terminare: è l'unica configurazione che destabilizza davvero la run.

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
- tool call riuscite e fallite, e fallimenti consecutivi;
- numero di replan totali e presenza di una motivazione;
- nuove tool call, osservazioni o step completati dall'ultimo replan;
- revisioni della stessa operazione esterna;
- ripresentazione dello stesso candidato o di un payload già respinto.

Una differenza rispetto alla v1: **gli interventi umani non contano come nuova base osservabile.** Nella v1 `human_review_count` entrava nel delta di `observable_change_since_last_replan`, quindi una review umana azzerava gratuitamente il vincolo di liveness. Una decisione umana non è un fatto nuovo sul mondo.

### Contratto di consumo dei fatti

Il contratto runtime corrente esporta 22 `raw_facts`. Dieci sono consumati direttamente
da Trajectory/OPA per la convergenza: `needs_replan_signal`, `replan_reason_present`,
`first_replan_request`, `observable_change_since_last_replan`,
`external_action_revision_count`, `unchanged_resubmission`,
`repeated_rejected_payload_digest`, `replan_count`, `consecutive_tool_failures` e
`action_churn_count`. `replan_count` appartiene quindi al percorso decisionale attivo.

I restanti dodici — `actor_turn_count`, `progress_epoch`, `completed_step_count`,
`tool_call_count`, `successful_tool_call_count`, `failed_tool_call_count`,
`observation_count`, `consecutive_provider_protocol_failures`, `completion_signal`,
`external_candidate`, `provider_recovery_active` e
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
| `replan_ceiling_reached` | replan totali oltre il limite configurato | `WAIT_HUMAN_REVIEW` |
| `consecutive_tool_failures` | N fallimenti di fila senza osservazione utile | `REPLAN`, poi `WAIT_HUMAN_REVIEW` |
| `action_churn` | oscillazione fra varianti già viste senza convergere | `WAIT_HUMAN_REVIEW` |
| `unchanged_resubmission` | stesso payload e stessa selezione ripresentati | `WAIT_HUMAN_REVIEW` |
| `repeated_rejected_payload_digest` | ripresentato un digest già respinto | `WAIT_HUMAN_REVIEW` |
| `action_revision_limit_reached` | revisioni oltre `max_action_revisions_before_review` | `WAIT_HUMAN_REVIEW` |

`replan_ceiling_reached`, `consecutive_tool_failures` e `action_churn` sono nuove. Le altre esistono già e funzionano: `unchanged_resubmission` e `repeated_rejected_payload_digest` sono la parte più solida del sistema attuale.

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

OPA restituisce `WAIT_HUMAN_REVIEW`. Se invece la base cambia ogni volta ma i replan continuano a susseguirsi, è `replan_ceiling_reached` a fermare la run — nella v1 `replan_count` è calcolato e mai letto dalle policy.

### Esempio di proposta esterna

L'Actor propone l'invio di un'email. Il Judge richiede una revisione, il runtime registra il tentativo e scrive il feedback. L'Actor revisiona: nuovo payload, nuovo digest, `external_action_revision_count = 1`. Il Judge ri-segnala lo stesso difetto sostanziale. L'Actor ripresenta il payload identico:

```text
unchanged_resubmission            = true
repeated_rejected_payload_digest  = true
action_revision_limit_reached     = true
```

OPA restituisce `WAIT_HUMAN_REVIEW` con reason code `rejected_candidate_resubmitted`, e `rejected_alternatives` conserva le altre due. Questo percorso è già osservato end-to-end nelle run di benchmark: è il ciclo di revisione limitato che chiude correttamente.

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
| Criterio di Evidence | integrità dei riferimenti scelti | integrità **più** onere probatorio dichiarato dallo step |
| Selezione vuota | mai invalida | invalida se lo step ha dichiarato un onere |
| Esito di Evidence | un booleano, un reason code | fatti grezzi e deficit codes, con mapping OPA a direttive osservabili |
| `REQUEST_MORE_EVIDENCE` | senza feedback, senza contatore, senza tetto | feedback all'Actor, contatore, escalation a revisione umana |
| Ancoraggio del contenuto | assente | letterale sui token estraibili, con distinzione selezione/universo |
| Non-circolarità | assente | esposta come fatto strutturale; `circular_reference_detected` è il punto di estensione per il rilevamento dei riferimenti auto-prodotti |
| Regole di convergenza | solo liveness e ripresentazione | più tetto sui replan, fallimenti consecutivi, churn |
| Interventi umani nella liveness | contano come nuova base | non contano |
| Regole concorrenti | una sola riportata | decisiva riportata, altre in `rejected_alternatives` |
| Limite di revisione | scatta a limite meno uno | `action_revision_limit_reached` scatta al limite |

### Ordine di adozione

L'ordine conta perché Evidence e Trajectory partecipano ora al percorso attivo con direttive OPA osservabili e tracciate.

1. **Trajectory always-on**: produce fatti di liveness e convergenza su tutti gli eventi dell'Actor, con vincoli decisionali ai confini di replan, completamento e proposta esterna.
2. **Evidence event-sensitive**: è richiesto per gli step fattuali con burden dichiarato, per gli step completati e per i candidati di external operation ammessi; resta non richiesto per output intermedi e proposte non fattuali.
3. **Feedback e tetti**: `REQUEST_MORE_EVIDENCE` incrementa il contatore e si trasforma in `WAIT_HUMAN_REVIEW` al limite configurato.
4. **Judge separato**: OPA ammette il candidato solo dopo Evidence e Trajectory; il Judge valuta poi il significato e l'appropriatezza del contenuto quando è richiesto.
5. **Risk e Behavioral restano shadow-only**: forniscono segnali e conseguenze operative senza autorità sulla direttiva finale.

Il criterio di non-regressione è la sequenza di direttive e reason code delle run di benchmark. Una ripresentazione invariata viene instradata a revisione umana; la classificazione finale della terminazione dipende dalla decisione umana o dall'esito del runtime.
