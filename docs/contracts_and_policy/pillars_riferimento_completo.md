# I quattro pilastri — guida di riferimento completa

Come Static Policy, Evidence, Trajectory e Judge trasformano ogni turno dell'attore in
fatti tipizzati — e come OPA, l'unica autorità decisionale, li traduce in una direttiva.
Con soglie esatte, campi reali del codice, ed esempi presi da run vere.

> Generato dallo stato effettivo del codice (`configs/opa/layer2_governance/`,
> `src/layer2_runtime_governance/signal_services/`), non da una descrizione a memoria.
> Versione policy: `layer2_governance_v6` / `layer2_governance_thresholds_v3`.

---

## Indice

1. [Il quadro generale](#1-il-quadro-generale)
2. [Static Policy](#2-static-policy)
3. [Evidence](#3-evidence)
4. [Trajectory](#4-trajectory)
5. [Judge](#5-judge)
6. [L'ordine di precedenza completo](#6-lordine-di-precedenza-completo)
7. [Tutte le direttive, in un colpo d'occhio](#7-tutte-le-direttive-in-un-colpo-docchio)

---

## 1. Il quadro generale

Un principio, prima di ogni dettaglio: **OPA è l'unica autorità decisionale.** Nessun
pilastro decide nulla. Ognuno osserva il turno appena concluso e produce *fatti
tipizzati* — booleani, conteggi, liste di codici. Python non ricombina mai quei fatti in
una decisione propria: li spedisce a OPA così come sono, e applica solo la direttiva
tipizzata che torna indietro.

```mermaid
flowchart TD
    A["Turno dell'attore si conclude<br/>(propose_action / step_completed / needs_replan / ...)"] --> B["Runtime costruisce i base_facts<br/>(task, policy, operazione esterna se presente)"]
    B --> C{{"OPA: required_pillars<br/>quali pilastri sono attivi ora?"}}
    C --> D["Static Policy<br/>sempre valutato"]
    C --> E["Trajectory<br/>sempre attivo"]
    C --> F["Evidence<br/>attivo se contenuto fattuale<br/>o candidato esterno"]
    C -.se ammesso.-> G["Judge<br/>solo su candidato esterno già pulito"]
    D --> H{{"OPA: decision<br/>catena di precedenza"}}
    E --> H
    F --> H
    G --> H
    H --> I["Una direttiva tipizzata<br/>continue / request_more_evidence / replan /<br/>revise_action / wait_human_review / stop / ..."]
    I --> J["Il runtime applica la direttiva.<br/>Mai una seconda decisione in Python."]
```

I quattro pilastri lavorano in parallelo sullo stesso turno; solo OPA li ordina in una
direttiva unica.

| Pilastro | Domanda a cui risponde | Natura | Può bloccare da solo? |
|---|---|---|---|
| **Static Policy** | Questa operazione viola un vincolo assoluto e non negoziabile? | deterministico | Sì — `hard_deny` è assorbente |
| **Evidence** | Le affermazioni dell'agente sono agganciate a prove concrete? | deterministico | Sì — due classi: `evidence_violation` blocca, `evidence_concern` chiama un umano |
| **Trajectory** | L'esecuzione sta convergendo o gira a vuoto? | deterministico | Sì — via i tetti di convergenza |
| **Judge** | Questo esatto messaggio ha senso ed è appropriato da eseguire? | probabilistico (LLM) | Sì — ma solo dopo che gli altri tre sono puliti |

> **Risk** e **Behavioral** esistono nel sistema ma restano *shadow*: producono fatti
> osservabili (per l'audit) ma il loro `decision_influence` è sempre `false` — non
> possono mai spostare una direttiva. Non sono trattati come pilastri in questo
> documento per questo motivo.

---

## 2. Static Policy

`01` · deterministico

L'unico controllo che non guarda mai il contenuto delle affermazioni: guarda solo *chi*,
*che tipo di operazione* e *verso dove*. È il più semplice dei quattro, e per questo il
più assoluto: se scatta un `hard_deny`, nessun altro pilastro può salvare la run.

### 2.1 Quando si attiva

Sempre, a ogni passaggio di governance. Non ha un interruttore condizionale come Evidence.

### 2.2 Cosa controlla

```mermaid
flowchart TD
    A["Modalità read-only attiva<br/>E effetto mutante (create/update/delete/send/...)<br/>E l'operazione è un candidato eseguibile"] -->|sì| DENY["hard_deny = true"]
    B["Profilo database read-only<br/>MA lo statement non è una SELECT<br/>o è ambiguo"] -->|sì| DENY
    C["Destinatario in prohibited_destinations<br/>o dominio in prohibited_destination_domains<br/>o destination_class = prohibited"] -->|sì| DENY
    DENY --> STOP["direttiva: stop<br/>(assorbente, nessun altro pilastro viene consultato)"]
    A -->|no| RO{{"read_only_mode attivo<br/>o effetto = read su boundary non locale?"}}
    B -->|no| RO
    C -->|no| RO
    RO -->|sì| CRO["direttiva: continue_read_only"]
    RO -->|no| CLR{{"l'attore ha chiesto<br/>chiarimento?"}}
    CLR -->|sì| ASK["direttiva: ask_user_clarification"]
    CLR -->|no| PASS["passa il turno agli altri pilastri"]
```

Tre condizioni di `hard_deny`, ciascuna assorbente. Sotto, l'inviluppo read-only e la
richiesta di chiarimento.

### 2.3 Campi prodotti

| Campo | Tipo | Cosa dice |
|---|---|---|
| `hard_deny` | bool | Almeno una violazione assoluta è scattata |
| `read_only_required` | bool | Il turno deve restare nell'inviluppo read-only |
| `clarification_signaled` | bool | L'attore ha esplicitamente chiesto un chiarimento |
| `constraints` | lista | Vincoli attivi in questo turno (es. `execution_policy_read_only`) |
| `violations` | lista | Violazioni rilevate (es. `prohibited_destination`) |
| `reason_codes` | lista | Codici che finiscono sulla decisione OPA |
| `summary` | stringa | Spiegazione leggibile, una frase |

### 2.4 Configurazione — da `data.json`

```json
"static_policy": {
  "prohibited_destinations": ["blocked@example.com"],
  "prohibited_destination_domains": ["blocked.example"],
  "read_only_profile_ids": ["enterprise_db.read_only"]
}
```

Il profilo database read-only **non** è scritto dentro la regola Rego: è qui, in
configurazione. Un nuovo profilo va dichiarato per essere riconosciuto, altrimenti passa
il cancello in silenzio — una correzione fatta proprio in questa sessione di lavoro.

---

## 3. Evidence

`02` · deterministico · condizionale

Verifica che quello che l'agente afferma poggi su osservazioni reali raccolte dai tool.
**Non valuta se un'affermazione è vera** — quello lo fa il Judge, quando serve. Valuta se
è *sostenuta*: c'è un'osservazione concreta dietro ogni claim?

### 3.1 Quando si attiva

Non sempre — a differenza di Static e Trajectory. Si accende quando:

| Condizione | Campo che la determina |
|---|---|
| L'attore dichiara un passo completato | `actor_result_type == step_completed` |
| L'attore propone un'azione con contenuto fattuale | `actor_result_type == propose_action` e `has_factual_content == true` |
| Il passo prepara l'operazione che esce dal sistema | `produces_governed_external_effect_proposal == true` |

Il terzo caso è una correzione di questa architettura: prima, solo il primo flag
(`produces_factual_claims`) accendeva i controlli. Il passo che prepara l'email da
inviare non dichiarava mai claim fattuali — quindi il testo che usciva davvero dal
sistema restava fuori dal controllo. Ora entrambi i flag accendono le famiglie 2–5.

### 3.2 Le cinque famiglie di controllo

```mermaid
flowchart TD
    START(["Evidence attivo su questo turno"]) --> F1
    subgraph F1["Famiglia 1 · Integrità referenziale"]
      direction TB
      f1a["I riferimenti citati esistono, sono unici,<br/>appartengono a passi noti, hanno contenuto"]
      f1b["Il candidato è sigillato sui digest giusti<br/>(snapshot + selezione + operazione)"]
    end
    F1 --> F2
    subgraph F2["Famiglia 2 · Copertura dell'onere probatorio"]
      direction TB
      f2a["min_independent_sources raggiunto?"]
      f2b["require_retrieved_content soddisfatto?"]
      f2c["OPPURE: insufficienza dichiarata esplicitamente<br/>quando il task lo permette"]
    end
    F2 --> F3["Famiglia 3 · Qualità strutturale<br/>non tutta la selezione è fatta di soli titoli di ricerca"]
    F3 --> F4["Famiglia 4 · Non-circolarità<br/>l'agente non cita il proprio output come prova"]
    F4 --> F5["Famiglia 5 · Ancoraggio letterale<br/>ogni URL, data, numero o valuta nel testo<br/>deve comparire nelle osservazioni o venire dal task"]
    F5 --> SPLIT{{"letterale non ancorato:<br/>è un locator (URL) o un valore?"}}
    SPLIT -->|locator| VIOL["evidence_violation<br/>l'attore può recuperarlo o toglierlo"]
    SPLIT -->|valore| CONC["evidence_concern<br/>nessun recupero lo risolve"]
    F1 -.fallita.-> VIOL
    F2 -.fallita.-> VIOL
    F3 -.fallita.-> VIOL
    F4 -.fallita.-> VIOL
    VIOL & CONC --> GAP["evidence_gap = true<br/>(unione delle due classi)"]
```

Le famiglie sono valutate insieme, non in sequenza bloccante: `deficit_codes` può
contenerne più di una alla volta. Le famiglie 1–4 producono sempre `evidence_violation`
quando falliscono: sono verificabili, non c'è interpretazione. La famiglia 5 si divide
per tipo di token — un URL citato ma mai recuperato è un `evidence_violation` (l'attore
lo fetcha o lo toglie); un numero, una data o una valuta non ancorati sono un
`evidence_concern`, perché nessun recupero ulteriore trasforma un valore calcolato in
uno citato, e l'unica mossa dell'attore sarebbe cancellare un'affermazione che potrebbe
essere corretta. `evidence_gap` resta il nome storico dell'unione delle due classi: un
qualunque deficit continua a bloccare il completamento e l'ammissione al Judge,
qualunque sia la sua classe. F5 non considera più l'SQL di un'operazione database come
testo da ancorare: una query è una richiesta, non un'affermazione sul mondo.

### 3.3 Cosa succede dopo un deficit

```mermaid
flowchart TD
    A["evidence_gap = true"] --> B{{"evidence_violation = true?"}}
    B -->|sì| C{{"evidence_request_episode_count<br/>≥ 3?"}}
    C -->|no| D["OPA: request_more_evidence<br/>torna all'attore con i deficit_codes rimediabili"]
    C -->|sì| E["OPA: wait_human_review<br/>reason: evidence_request_limit_reached"]
    D --> F["L'attore corregge, ripropone il completamento"]
    F --> A
    B -->|no, solo concern| G["OPA: wait_human_review<br/>reason: evidence_anchoring_requires_review<br/>(nessun ciclo, va diretto a un umano)"]
    G --> H{{"un umano approva<br/>questa identica selezione?"}}
    H -->|sì| I["adjudicated_evidence_anchoring_digest<br/>registra il digest approvato"]
    H -->|no, replan| J["il digest si azzera:<br/>una nuova traiettoria produce claim diversi"]
```

Un `evidence_violation` (famiglie 1–4, o un locator di famiglia 5) segue il ciclo
limitato all'attore: tetto per episodio, si azzera a ogni nuova operazione. Un
`evidence_concern` puro (solo valori di famiglia 5, nessuna violazione) **salta il
ciclo**: non ha senso rimandarlo all'attore, perché nessun tentativo aggiuntivo può
risolverlo. Va diretto a revisione umana con `evidence_anchoring_requires_review`.

La memoria di aggiudicazione evita che lo stesso concern torni a bloccare la run dopo
un'approvazione: quando un umano approva un completamento fermato per ancoraggio,
`adjudicated_evidence_anchoring_digest` registra il digest esatto della selezione di
evidenze approvata. Un concern sulla stessa identica selezione non si ripresenta. Un
`replan`, che produce claim diversi, azzera la memoria.

### 3.4 I `deficit_codes` — il vocabolario che arriva alla decisione

I codici specifici viaggiano **sulla decisione stessa** (non restano chiusi dentro il
pilastro), separati per classe: `request_more_evidence` porta solo i codici
**rimediabili** (`violation_reason_codes`); `wait_human_review` per un ancoraggio porta
solo i codici di **concern** (`concern_reason_codes`), sotto
`evidence_anchoring_requires_review`. Un deficit di famiglia 5 non viaggia mai insieme a
un deficit rimediabile sulla stessa decisione: dire all'attore "correggi X" e "rimuovi
questi valori" nello stesso turno rischia di fargli cancellare un'affermazione corretta
solo per soddisfare il controllo automatico. La valutazione finale della tesi può
distinguere quale famiglia interviene *e* quale classe ha deciso — non solo che
"qualcosa" è mancato.

| Codice | Famiglia | Classe | Cosa significa |
|---|---|---|---|
| `invalid_evidence_ref` | 1 | violation | Il riferimento citato non esiste tra le osservazioni |
| `duplicate_evidence_ref` | 1 | violation | La stessa referenza citata più volte |
| `empty_observation_content` | 1 | violation | L'osservazione citata non ha contenuto |
| `unresolvable_provenance` | 1 | violation | Non si risale a chi/cosa ha prodotto l'osservazione |
| `unknown_step_lineage` | 1 | violation | L'osservazione non appartiene a un passo noto del piano |
| `candidate_digest_mismatch` | 1 | violation | Il candidato non è sigillato sui digest correnti |
| `missing_evidence_burden` | 2 | violation | Il passo produce claim fattuali ma non dichiara un onere probatorio |
| `insufficient_independent_sources` | 2 | violation | Meno fonti indipendenti del minimo richiesto |
| `retrieved_content_missing` | 2 | violation | Serve contenuto davvero recuperato, non solo uno snippet |
| `title_only_evidence_fragility` | 3 | violation | La selezione è fatta di soli titoli di ricerca |
| `circular_evidence_reference` | 4 | violation | L'agente cita un proprio output precedente come prova |
| `unanchored_locators_in_selection` | 5 | violation | Un URL citato nel testo selezionato non è mai stato recuperato |
| `unanchored_locators_in_universe` | 5 | violation | Lo stesso, e il locator non compare in *nessuna* osservazione raccolta |
| `unanchored_literals_in_selection` | 5 | **concern** | Un valore (numero, data, valuta) nel testo selezionato non è sostenuto da nessuna fonte né dal task |
| `unanchored_literals_in_universe` | 5 | **concern** | Lo stesso, e il valore non compare in *nessuna* osservazione raccolta |

I codici di famiglia 5 sono l'unica coppia che si divide per classe: un URL non
recuperato è un `violation` (l'attore lo fetcha o lo toglie), un valore non ancorato è
un `concern` (va a un umano, mai all'attore).

> **Limite dichiarato.** Una sesta famiglia — **freschezza della fonte** (finestra di
> giorni, data di pubblicazione) — è stata **ritirata** in questa sessione. I codici
> `freshness_metadata_missing`, `stale_evidence`, `future_source_timestamp` restano
> dichiarati nello schema della traccia (chiuso, per compatibilità con le run archiviate)
> ma nessun codice attuale li produce più: si attivava solo su un vincolo esplicito
> "ultimi N giorni" nel task, nessuno scenario del pool di valutazione lo dichiara, e nel
> frattempo — con l'enforcement disattivato da configurazione — continuava comunque a
> generare deficit inviati all'attore. Causa diretta di un loop osservato su una run.

### 3.5 Soglie — da `data.json`

| Soglia | Valore | Cosa limita |
|---|---|---|
| `max_evidence_requests_before_review` | **3** | richieste di evidenza per episodio prima dell'escalation a revisione umana |

Le altre due dimensioni dell'onere probatorio non sono soglie globali: sono **dichiarate
dal compito** (`min_independent_sources`, `require_retrieved_content`) o derivate dal
suo testo, e il planner può solo alzarle, mai abbassarle.

### 3.6 Esempio reale — un deficit catturato

```
run benchmark-web-email-13 · deficit_codes attivi

"unanchored_literals_in_selection": [
  "141.006", "30,4%", "43,6%", "85,8%", "97%", "97,0%"
]
```

Numeri scritti in italiano, la fonte in inglese diceva `141,006` e `30.4%`: prima
venivano confrontati come stringhe e risultavano "inventati" anche se corretti.
L'ancoraggio letterale ora confronta la **quantità**, non la forma: separatori di
migliaia/decimali diversi per lingua e zeri finali (`97%` = `97.0%`) non generano più
falsi allarmi. Un valore genuinamente non ancorato come questo — se il matching di
quantità non lo salva — è oggi un `evidence_concern`: va a revisione umana invece di
tornare all'attore, perché non è detto sia inventato. Nella run `benchmark-web-email-14`
il caso reale era una data (`28/07/2026`) calcolata dall'attore come inizio della
finestra "ultimi 30 giorni dal 27 agosto 2026" richiesta dal task — corretta
aritmeticamente, ma non citabile da nessuna fonte per costruzione.

### 3.7 Campi completi esportati

| Campo | Stato | Cosa dice |
|---|---|---|
| `observation_count` | audit | Totale osservazioni raccolte finora nella run |
| `observation_ids_unique` | famiglia 1 | Nessun id di osservazione duplicato |
| `selected_evidence_ref_count` | audit | Quante referenze l'attore ha selezionato |
| `selected_evidence_refs_valid` / `..._unique` | famiglia 1 | Le referenze esistono e non si ripetono |
| `selected_observations_have_content` | famiglia 1 | Ogni referenza ha contenuto concreto |
| `selected_observations_provenance_resolvable` | famiglia 1 | La provenienza di ogni referenza è risolvibile |
| `selected_observations_belong_to_known_steps` | famiglia 1 | Ogni referenza appartiene a un passo noto |
| `selected_evidence_all_title_only` | famiglia 3 | Selezione fatta di soli titoli, mai contenuto |
| `evidence_selection_digest` / `evidence_snapshot_digest` | famiglia 1 | Impronte usate per il triplo vincolo col Judge |
| `candidate_evidence_binding_required` / `..._present` / `..._snapshot_matches` / `..._selection_matches` / `candidate_external_operation_digest_matches` | famiglia 1 | Il candidato di completamento è sigillato sui digest correnti |
| `completion_attempt` / `factual_completion_attempt` | audit | Il turno chiude il passo / dichiara fatti |
| `evidence_completion_attempt` | **nuovo interruttore** | Il passo va controllato: fattuale **oppure** con effetto esterno |
| `evidence_request_count` / `evidence_request_episode_count` / `evidence_request_episode_id` | audit | Contatori di richieste, globale e per episodio |
| `produces_factual_claims` / `produces_governed_external_effect_proposal` | attivazione | I due flag dichiarati dal passo del piano |
| `evidence_families_active` | attivazione | Almeno uno dei due flag è vero |
| `evidence_burden_derived_from_task` | audit | L'onere è stato derivato dal testo del task, non dal planner |
| `factual_step_missing_evidence_burden` | famiglia 2 | Claim fattuali dichiarati senza alcun onere probatorio |
| `coverage_evaluated` | audit | La copertura è stata effettivamente valutata |
| `distinct_independent_sources_count` | famiglia 2 | Origini distinte tra le fonti *selezionate* |
| `retrieved_independent_sources_count` | diagnostico | Origini distinte nell'*intero universo* osservato (mai decide da sola) |
| `min_independent_sources_required` | famiglia 2 | La soglia effettiva applicata a questo passo |
| `independent_sources_satisfied` / `retrieved_content_satisfied` | famiglia 2 | Le due metà della copertura |
| `burden_satisfied` | famiglia 2 | Copertura complessiva soddisfatta |
| `insufficiency_declaration_permitted` / `..._declared_in_payload` | famiglia 2 | Il task permette la valvola "dichiara l'insufficienza", e se è stata usata |
| `circular_reference_detected` / `circular_evidence_observation_ids` | famiglia 4 | Autocitazione rilevata, e su quali id |
| `literals_anchored_to_task` | audit | Valori del testo che vengono dal task, quindi non da provare |
| `unanchored_locators_in_selection` / `..._in_universe` | famiglia 5 · violation | URL citati mai recuperati, sulla selezione e sull'intero universo |
| `has_unanchored_locators_in_selection` / `..._in_universe` | famiglia 5 · violation | Le stesse liste, come booleani |
| `unanchored_literals_in_selection` / `..._in_universe` | famiglia 5 · concern | Valori non sostenuti (non URL), sulla selezione e sull'intero universo |
| `has_unanchored_literals_in_selection` / `..._in_universe` | famiglia 5 · concern | Le stesse liste, come booleani |
| `anchoring_concern_adjudicated` | audit | Un umano ha già approvato questa identica selezione per ancoraggio |
| `deficit_codes` | **verso la decisione** | L'aggregato di tutti i deficit trovati — propagato oltre il pilastro |
| `evidence_violation` / `evidence_concern` | **result, verso la decisione** | Le due classi separate; `evidence_gap` ne resta l'unione |
| `violation_reason_codes` / `concern_reason_codes` | **result, verso la decisione** | I `deficit_codes` già partizionati per classe |

---

## 4. Trajectory

`03` · deterministico

Guarda la **dinamica** dell'esecuzione, mai il contenuto: l'agente sta avanzando o sta
ripetendo se stesso? Sempre attivo, ma con due modalità — sui turni esplorativi osserva
solo la vitalità, nei punti di impegno (completamento, proposta esterna) aggiunge le
regole di convergenza vere e proprie.

### 4.1 Due categorie di fatti, dichiarate esplicitamente

Una scelta di progetto centrale: la policy elenca apertamente quali fatti possono
muovere una direttiva (`policy_fact_names`) e quali restano **sola telemetria**
(`observational_only_fact_names`). Narrazione, lunghezza, tempo di riflessione: mai
degrado, per costruzione.

| Fatti di policy — possono decidere | Fatti osservazionali — solo audit |
|---|---|
| `needs_replan_signal`, `replan_reason_present`, `first_replan_request`, `observable_change_since_last_replan`, `external_action_revision_count`, `unchanged_resubmission`, `repeated_rejected_payload_digest`, `unresolvable_revision`, `replan_episode_count`, `consecutive_tool_failures`, `action_churn_count`, `external_candidate`, `current_step_tool_attempt_count`, `completion_signal` | `actor_turn_count`, `progress_epoch`, `completed_step_count`, `tool_call_count`, `successful_tool_call_count`, `failed_tool_call_count`, `synthetic_tool_result_count`, `observation_count`, `replan_count`, `consecutive_provider_protocol_failures`, `provider_recovery_active`, `provider_protocol_retry_exhausted` |

### 4.2 I sei guardiani di convergenza

```mermaid
flowchart TD
    T(["Trajectory attivo"]) --> G1{{"replan_episode_count ≥ 3?"}}
    G1 -->|sì| R1["wait_human_review<br/>replan_ceiling_reached"]
    G1 -->|no| G2{{"consecutive_tool_failures ≥ 4?"}}
    G2 -->|sì| R2["wait_human_review<br/>consecutive_tool_failures"]
    G2 -->|no| G3{{"current_step_tool_attempt_count ≥ 30<br/>E il turno non sta completando?"}}
    G3 -->|sì| R3["wait_human_review<br/>step_exploration_budget_exhausted"]
    G3 -->|no| G4{{"action_churn_count ≥ 2<br/>sulla stessa operazione esterna?"}}
    G4 -->|sì| R4["wait_human_review<br/>action_churn"]
    G4 -->|no| G5{{"replan chiesto ma NON ammissibile<br/>(nessuna base osservativa nuova)?"}}
    G5 -->|sì| R5["wait_human_review<br/>replan_request_stalled"]
    G5 -->|no| G6{{"Judge ha chiesto una revisione<br/>senza nuova base osservativa?"}}
    G6 -->|sì| R6["wait_human_review<br/>unresolvable_revision"]
    G6 -->|no| G7{{"replan chiesto ED ammissibile?"}}
    G7 -->|sì| OK["replan — concesso,<br/>non è una review"]
    G7 -->|no| PASS(["nessun guardiano scattato,<br/>il turno prosegue"])
```

L'ordine qui rispecchia la precedenza reale in `main.rego`: dall'alto verso il basso, il
primo guardiano che scatta vince.

> **Il guardiano 03 è nuovo** di questa sessione. Prima, nessun limite fermava
> l'esplorazione read-only: una run è rimasta bloccata su un passo per 30+ chiamate,
> tutte riuscite, perché le ricerche non fallivano (quindi `consecutive_tool_failures`
> restava a zero) e i contatori di turno sono per progetto sola telemetria. Il guardiano
> anti-ripetizione esisteva già, ma contava le ripetizioni nella `progress_epoch`
> corrente — e ogni osservazione nuova fa avanzare quell'epoch, disarmando la guardia.
> È stata spostata sul passo del piano, e il nuovo tetto copre il caso che la guardia da
> sola non può vedere: un attore che *varia* le richieste invece di ripeterle.

> **Il guardiano 01 aveva un buco.** `replan_episode_count ≥ max_replans_before_review`
> scattava solo quando, nello stesso turno, `needs_replan_signal` era vero — cioè solo
> quando l'attore stava esplicitamente chiedendo un altro replan. Ma il *protocol
> replan* che il Tool Gateway avvia da sé (§4.5) non arriva mai a governance in quello
> stato: installa una traiettoria sostitutiva e, al turno successivo, l'attore ha già
> prodotto un risultato nuovo. Il contatore contava questi replan, la regola li
> ignorava: una run poteva sostituire la propria traiettoria senza limite finché nessun
> replan veniva mai *richiesto* dall'attore nello stesso turno in cui il tetto era già
> raggiunto. La condizione `E l'attore chiede un altro replan` è stata rimossa: il
> guardiano ora conta *quante traiettorie l'episodio ha già sostituito*, non chi sta
> chiedendo la prossima.

### 4.3 Soglie — da `data.json`

| Soglia | Valore | Cosa limita |
|---|---|---|
| `max_replans_before_review` | **3** | replan nello stesso episodio |
| `max_action_revisions_before_review` | **3** | revisioni sulla stessa proposta esterna |
| `max_action_churn_before_review` | **2** | oscillazioni tra varianti della stessa operazione |
| `max_consecutive_tool_failures_before_review` | **4** | fallimenti di tool consecutivi (solo backend, mai sintetici) |
| `max_tool_attempts_per_step_before_review` | **30** | tentativi sul passo corrente, inclusi i rifiuti locali — **nuova** |

### 4.4 Esempio reale — una run sana

```
run benchmark-web-email-14 · step_8, turno 12

"tool_call_count": 12,
"current_step_tool_attempt_count": 0,
"consecutive_tool_failures": 0,
"replan_count": 0,
"action_churn_count": 0
```

Le 12 chiamate sono concentrate tutte negli step di ricerca (2–3); dal passo 5 in poi il
piano chiede solo sintesi su dati già ancorati, quindi `current_step_tool_attempt_count`
resta a zero a ogni nuovo passo invece di accumularsi. Un contatore piatto qui è il
segnale buono, non quello sospetto.

### 4.5 Come nasce una `disposizione` di tool — il meccanismo sotto il guardiano 03

Ogni tentativo di chiamare un tool riceve una `ToolDisposition` tipizzata. La
distinzione che conta per Trajectory è una sola: **ha raggiunto il backend o no?**

| Disposizione | Ha raggiunto il backend? | Quando |
|---|---|---|
| `executed_success` | sì | Chiamata riuscita |
| `executed_retryable_failure` | sì | Fallimento temporaneo (timeout, indisponibilità) |
| `executed_terminal_failure` | sì | Fallimento definitivo (es. 404, 403) |
| `cache_hit` | no | Risultato già disponibile localmente |
| `rejected_duplicate` | no | Richiesta identica già esaurita in questo episodio |
| `rejected_circuit` | no | Circuito aperto dopo troppi fallimenti temporanei |
| `rejected_unanchored_target` | no | Il bersaglio richiesto non è mai stato osservato |

Ogni nuovo tipo di rifiuto locale ricade automaticamente sotto la stessa guardia di
progresso, per costruzione — non serve più elencare a mano quali disposizioni contano.

### 4.6 Il Tool Gateway rifiuta, OPA decide quanto è troppo

Il Tool Gateway ha una propria guardia locale, indipendente da Trajectory: se un
bersaglio (`action_key`) accumula **3 tentativi risolti localmente nell'intera run**
(`_MAX_SETTLED_ATTEMPTS_PER_TARGET`), diventa *esaurito*. Da quel momento ogni ulteriore
richiesta identica riceve un rifiuto sintetico esplicitamente terminale — "non verrà
più tentata, scegli un altro bersaglio" — invece del messaggio di rifiuto ordinario, e
il passo del piano viene **deliberatamente mantenuto** invece di essere sostituito: il
suo budget di esplorazione (guardiano 03) continua a salire fino al tetto.

Questo rifiuto è enforcement, non governance: il gateway decide se *questa* esecuzione
può partire, non quante traiettorie sostituite sono troppe. Quella soglia resta di OPA,
sul guardiano 01 (§4.2–§4.3). La separazione regge perché un bersaglio esaurito
concede **al massimo un replan** prima di bloccarsi da solo: il *protocol replan* del
gateway scatta dopo 2 ripetizioni sullo stesso passo, e a quel punto il bersaglio non è
ancora esaurito (2 < 3); ma sul passo successivo la ripetizione riparte da un contatore
di passo azzerato mentre il contatore di run continua a salire, quindi il secondo
tentativo di replan sullo stesso bersaglio trova il gateway già esaurito e non genera
un secondo replan. Un attore fissato su bersagli *diversi* può comunque accumulare più
protocol replan — ed è esattamente lì che il guardiano 01 di OPA, ora corretto (§4.2),
interviene: la soglia direttiva su "quanti replan sono troppi" vive in un solo posto.

> **Bug chiuso in questa sessione.** Prima, il gateway non aveva alcun tetto di run
> scope: un attore che deduceva un URL da un titolo (mai osservato come locator) veniva
> rifiutato, ripeteva la richiesta, il *protocol replan* azzerava lo step — e con lo
> step si azzerava l'unico contatore (`current_step_tool_attempt_count`) che avrebbe
> potuto far scattare il guardiano 03. La cura ricreava il loop un piano più sopra: una
> run ha ripetuto lo stesso URL inventato 26 volte senza che nessun guardiano potesse
> mai vederla. Il tetto di run scope sul gateway, insieme al fix del guardiano 01
> sopra, chiude entrambe le metà del buco.

`observable_change_since_last_replan` (tabella §4.1) aveva un difetto collegato: leggeva
la lunghezza di `tool_results_history`, che include anche gli esiti risolti localmente
— un *cache hit* bastava a soddisfare la guardia e ad ammettere il replan che la guardia
esiste per rifiutare. Ora legge `tool_call_count`, che avanza solo su un'invocazione
reale del backend — la stessa definizione già usata dal controllo gemello sulla
revisione del Judge (`unresolvable_revision`).

---

## 5. Judge

`04` · probabilistico (LLM)

L'unico controllo che legge il **significato**. Domanda: questo esatto messaggio, con
questo destinatario e questo contenuto, è fedele al compito, completo, fondato sulle
osservazioni, e appropriato da eseguire? È anche il più costoso — per questo è l'ultimo
cancello, mai il primo.

### 5.1 L'ammissione è essa stessa una regola di policy

Il Judge non viene invocato "quando serve" a discrezione di Python. OPA lo ammette solo
quando *tutte* queste condizioni sono vere insieme:

```mermaid
flowchart TD
    A["candidato di operazione esterna presente"] --> B{{"versione bundle e soglie<br/>corrispondono a quelle attese?"}}
    B -->|no| X["non ammesso<br/>(mismatch fail-closed)"]
    B -->|sì| C{{"Evidence e Trajectory<br/>sono richiesti e attivi?"}}
    C -->|no| X
    C -->|sì| D{{"nessun hard_deny statico<br/>nessuna richiesta di chiarimento"}}
    D -->|no| X
    D -->|sì| E{{"evidence_gap = false?"}}
    E -->|no| X
    E -->|sì| F{{"trajectory pulita:<br/>nessun replan bloccato/ammissibile/<br/>ripresentazione invariata o già rifiutata"}}
    F -->|no| X
    F -->|sì| G{{"il digest del payload<br/>è un hash valido a 64 caratteri?"}}
    G -->|no| X
    G -->|sì| ADMIT(["Judge ammesso e invocato"])
```

Il costo si paga una volta sola, e solo nel punto in cui può davvero contare qualcosa.

### 5.2 Cosa vede e cosa non vede

| Vede | Non vede |
|---|---|
| Il compito autoritativo, il piano completo, i passi già chiusi | Lo scratchpad interno dell'attore |
| *Tutte* le osservazioni canoniche, in un pacchetto sigillato con digest | Argomenti grezzi dei tool |
| Il payload esatto che verrebbe eseguito | Contatori, storia di retry e replan |
| I rilievi delle revisioni precedenti sulla stessa operazione | Le valutazioni dei pillar in ombra (risk, behavioral) |

### 5.3 Il triplo vincolo di digest

La parte più solida dell'intera architettura. Il Judge deve aver valutato *esattamente*
ciò che verrebbe eseguito — tre impronte devono combaciare tutte:

```mermaid
flowchart LR
    subgraph valutato["Ciò che il Judge ha valutato"]
      d1["evaluated_operation_digest"]
      d2["evaluated_evidence_snapshot_digest"]
      d3["evaluated_evidence_selection_digest"]
    end
    subgraph atteso["Ciò che verrebbe eseguito ora"]
      e1["expected_operation_digest"]
      e2["expected_evidence_snapshot_digest"]
      e3["expected_evidence_selection_digest"]
    end
    d1 -.deve combaciare.- e1
    d2 -.deve combaciare.- e2
    d3 -.deve combaciare.- e3
    d1 & d2 & d3 & e1 & e2 & e3 --> CHK{{"tutti e tre combaciano?"}}
    CHK -->|no| FAIL["judge_required_unavailable<br/>→ wait_human_review<br/>(fail-closed, sempre)"]
    CHK -->|sì| OK["binding valido<br/>il verdetto può contare"]
```

Se anche solo uno dei tre non combacia — l'attore ha modificato il payload dopo la
valutazione, per esempio — il sistema si chiude in sicurezza.

### 5.4 Dal verdetto alla direttiva

| Verdetto | Direttiva OPA | Condizione aggiuntiva |
|---|---|---|
| `safe` | continue | prosegue verso l'eventuale gate di approvazione |
| `needs_revision` | revise_action | salvo `action_revision_limit_reached` (soglia 3) → `wait_human_review` |
| `unsafe` | wait_human_review | sempre, nessuna eccezione |
| `uncertain` | wait_human_review | sempre, nessuna eccezione |

### 5.5 Campi del verdetto

| Campo | Cosa dice |
|---|---|
| `verdict` | `safe` · `needs_revision` · `unsafe` · `uncertain` |
| `severity` | `low` · `medium` · `high` · `critical` |
| `confidence` | Quanto è sicuro del proprio giudizio (0–1) |
| `findings` | Ogni rilievo cita sia un elemento preciso del compito sia il campo esatto del payload che lo contraddice |
| `claim_assessments` | Per ogni affermazione: sostenuta dalle fonti citate / sostenuta ma non citate / non sostenuta / contraddetta / contesto incompleto |
| `evaluated_operation_digest`, `evaluated_evidence_snapshot_digest`, `evaluated_evidence_selection_digest` | Le tre impronte del vincolo (5.3) |

Il Judge non può inventarsi referenze: ogni identificativo che cita deve stare nella
lista `allowed_evidence_ids` del pacchetto sigillato che gli è stato dato.

### 5.6 Esempio reale — un verdetto `safe`

```
run benchmark-web-email-14 · step_8, invio email

«The draft email matches the required recipient, exact subject, and substantive
Italian briefing content with direct links and comparison across four verified
sources. No concrete payload defect is evident from the authoritative evidence
provided.»
```

Direttiva risultante: comunque `wait_human_review` — ma non per il Judge. Il gate di
approvazione della capability (`capability_approval_required`) è un controllo separato e
successivo: anche un verdetto `safe` non salta l'autorizzazione umana esplicita se la
policy della capability la richiede.

---

## 6. L'ordine di precedenza completo

Tutto quello che hai letto sopra confluisce in un'unica catena, valutata dall'alto verso
il basso in `main.rego`. Il primo ramo che si applica vince: nessun pilastro successivo
viene nemmeno consultato.

```mermaid
flowchart TD
    S(["decisione richiesta"]) --> V{{"versione bundle o soglie<br/>non corrispondono?"}}
    V -->|sì| VX["wait_human_review<br/>policy_version_mismatch"]
    V -->|no| D{{"Static: hard_deny?"}}
    D -->|sì| DX["stop — assorbente"]
    D -->|no| JU{{"Judge richiesto<br/>ma non disponibile/non valido?"}}
    JU -->|sì| JUX["wait_human_review<br/>judge_required_unavailable"]
    JU -->|no| PU{{"un pilastro attivo<br/>è indisponibile?"}}
    PU -->|sì| PUX["wait_human_review<br/>required_pillar_unavailable"]
    PU -->|no| CL{{"Static: chiarimento<br/>richiesto?"}}
    CL -->|sì| CLX["ask_user_clarification"]
    CL -->|no| TJ["Trajectory: i sei guardiani<br/>(vedi sezione 4.2)"]
    TJ -->|uno scatta| TJX["replan oppure wait_human_review<br/>a seconda del guardiano"]
    TJ -->|nessuno scatta| JV{{"Judge: verdetto unsafe<br/>o uncertain?"}}
    JV -->|sì| JVX["wait_human_review"]
    JV -->|no| JR{{"Judge: needs_revision?"}}
    JR -->|sì e tetto revisioni raggiunto| JRX["wait_human_review<br/>action_revision_limit_reached"]
    JR -->|sì sotto il tetto| JRR["revise_action"]
    JR -->|no| EVV{{"Evidence: evidence_violation<br/>E tetto richieste raggiunto?"}}
    EVV -->|sì| EVX["wait_human_review<br/>evidence_request_limit_reached"]
    EVV -->|no| EVC{{"Evidence: evidence_concern<br/>senza evidence_violation?"}}
    EVC -->|sì| EVA["wait_human_review<br/>evidence_anchoring_requires_review"]
    EVC -->|no| EVR2{{"Evidence: evidence_violation?"}}
    EVR2 -->|sì| EVR["request_more_evidence"]
    EVR2 -->|no| LC{{"candidato esterno:<br/>confidenza attore troppo bassa?"}}
    LC -->|sì| LCX["wait_human_review<br/>external_low_actor_self_confidence"]
    LC -->|no| AP{{"candidato esterno:<br/>approvazione richiesta dalla capability?"}}
    AP -->|sì| APX["wait_human_review<br/>capability_approval_required"]
    AP -->|no| RO{{"Static: read_only_required?"}}
    RO -->|sì| ROX["continue_read_only"]
    RO -->|no| GO(["continue"])
```

Ordine esatto da `configs/opa/layer2_governance/main.rego`. Ogni nodo qui è uno dei
controlli già visti nelle quattro sezioni precedenti.

---

## 7. Tutte le direttive, in un colpo d'occhio

Il vocabolario completo che OPA può restituire. Ogni direttiva è tipizzata: il runtime
la applica, non la reinterpreta mai.

| Direttiva | Esito | Descrizione |
|---|---|---|
| `continue` | ✅ via libera | Tutti i pilastri attivi hanno dato l'assenso. Il turno prosegue normalmente. |
| `continue_read_only` | ✅ via libera | Prosegue, ma dentro l'inviluppo read-only (`static_policy`). |
| `request_more_evidence` | ↩️ torna all'attore | Evidence ha trovato un `evidence_violation`: l'attore riceve i `deficit_codes` rimediabili e corregge. Un `evidence_concern` puro non arriva mai qui — va diretto a `wait_human_review` (§3.3). |
| `revise_action` | ↩️ torna all'attore | Il Judge ha chiesto `needs_revision` sotto il tetto delle revisioni consentite. |
| `replan` | ↩️ torna all'attore | Trajectory ha ammesso una richiesta di replan legittima (base osservativa nuova). |
| `ask_user_clarification` | ⏸️ richiede input | L'attore stesso ha segnalato di aver bisogno di un chiarimento. |
| `wait_human_review` | ⏸️ richiede input | Uno qualsiasi dei guardiani/gate lo richiede — la causa esatta è sempre nel `reason_codes`. |
| `stop` | 🛑 bloccato | Static Policy ha rilevato un `hard_deny`. Assorbente: nessun altro pilastro viene consultato. |

> Ogni `wait_human_review` porta con sé un `reason_codes` preciso (es.
> `step_exploration_budget_exhausted`, `capability_approval_required`, `judge_unsafe`...).
> La dashboard mostra sempre quel codice, mai un generico "revisione necessaria" — è
> così che si distingue, guardando una run, *quale* pilastro ha chiesto la parola.
