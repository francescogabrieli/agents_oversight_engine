# I tre pillar: come funzionano ora e come funzioneranno

Documento di orientamento rapido su **Evidence**, **Trajectory** e **Judge**.
Non è la specifica: quella resta in `evidence_trajectory_pillars_it_v2.md` e in `AGENTS.md`.

Versione della policy descritta qui: `layer2_governance_v6` /
`layer2_governance_thresholds_v3` (`configs/opa/layer2_governance/data.json`). È la
configurazione **congelata** per la campagna di valutazione finale della tesi.

---

## Il quadro in una pagina

Dopo ogni turno dell'attore, il runtime raccoglie fatti tipizzati e li manda a OPA.
**OPA è l'unico che decide.** I pillar non decidono: producono fatti, OPA li traduce in
una direttiva.

I tre pillar rispondono a tre domande diverse:

| Pillar | Domanda | Natura |
|---|---|---|
| **Evidence** | Quello che l'agente afferma è agganciato a prove concrete? | deterministico |
| **Trajectory** | L'esecuzione sta convergendo o sta girando a vuoto? | deterministico |
| **Judge** | Questo esatto messaggio che sta per uscire ha senso ed è appropriato? | probabilistico (LLM) |

I primi due sono controlli **strutturali**: guardano forma, provenienza e dinamica, mai
il significato. Il Judge è l'unico che legge il significato, ed è per questo l'ultimo
cancello e il più costoso: viene invocato solo quando gli altri due sono già puliti.

L'ordine di precedenza in `main.rego` è: versione bundle → deny statico → pillar
indisponibile → chiarimento → **Trajectory** → **Judge** → **Evidence** → approvazione →
continua.

---

## Evidence

### A cosa serve

Verificare che le affermazioni dell'agente poggino su osservazioni reali raccolte dai
tool. Non valuta se un'affermazione è *vera*: valuta se è *sostenuta*.

### Quando si attiva

- quando l'attore dichiara un passo completato;
- quando propone un'azione che contiene contenuto fattuale;
- su ogni candidato di operazione esterna.

### Cosa controlla — quattro famiglie

1. **Integrità referenziale e digest** — le referenze citate esistono davvero, sono
   uniche, appartengono a passi noti, e il candidato è sigillato sui digest giusti.
2. **Copertura dell'onere probatorio** — numero minimo di fonti indipendenti, contenuto
   davvero recuperato.
3. **Qualità strutturale** — non basta una selezione fatta di soli titoli di ricerca.
4. **Non-circolarità** — l'agente non può citare il proprio output come prova.
5. **Ancoraggio letterale** — URL, date, importi e numeri presenti nel testo devono
   comparire nelle osservazioni, o venire dal compito stesso.

> Una quinta famiglia — **freschezza della fonte** (finestra di giorni, data di
> pubblicazione, source-timestamp) — è stata **ritirata**. Si attivava solo se il
> compito dichiarava esplicitamente "ultimi N giorni": su tutti e 24 gli scenari del pool
> di valutazione (`realistic_pool_v1` + `oversight_demo_v1`) non scatta mai. Nel
> frattempo, con l'enforcement disattivato da configurazione, continuava comunque a
> calcolare deficit e a mandarli all'attore — un caso in cui Python contraddiceva una
> decisione che OPA aveva già escluso, ed è la causa diretta di un loop osservato in una
> run di benchmark. I tre codici di deficit legati alla freschezza restano dichiarati
> nello schema della traccia (chiuso, nessuna proprietà libera) solo perché le tracce
> archiviate prima del ritiro li contengono ancora; nessun codice attuale li produce più.

### I campi in gioco — prima e dopo

**L'onere probatorio** (`evidence_burden`) è l'oggetto che dice *quanta prova serve* per
un passo. Vive sul passo del piano:

| Campo | Cosa significa | Prima | Dopo |
|---|---|---|---|
| `min_independent_sources` | Quante fonti di origine diversa servono almeno | scritto **dal planner** (ha messo `1`) | derivato **dal testo del compito**, il planner può solo alzarlo |
| `require_retrieved_content` | Non basta un risultato di ricerca, serve la pagina aperta davvero | c'era, ma un frammento di ricerca lo soddisfaceva | dipende dal tipo di osservazione, quindi funziona |
| `insufficiency_declaration_permitted` | Il compito permette di dichiarare "le fonti non bastano" | **non esisteva** | è la valvola che evita il blocco perpetuo |
| ~~`freshness_window_days`~~ | ~~Entro quanti giorni deve essere pubblicata la fonte~~ | c'era, con enforcement spento | **ritirato** — vedi sopra |

Un passo del piano dichiara inoltre due flag, entrambi già esistenti:

- `produces_factual_claims` — "questo passo produce affermazioni di fatto";
- `produces_governed_external_effect_proposal` — "questo passo prepara l'operazione che
  esce dal sistema" (esattamente uno per piano, e deve essere l'ultimo).

*Prima* solo il primo accendeva i controlli. *Dopo* li accende anche il secondo.

**I fatti che Evidence produce** e che OPA legge — sono i nomi che vedi nella traccia e
nella dashboard:

| Fatto | Cosa dice | Stato |
|---|---|---|
| `coverage_evaluated` | La copertura è stata valutata su questo passo | invariato |
| `min_independent_sources_required` | La soglia effettivamente applicata | invariato come nome, **cambia da dove viene il valore** |
| `distinct_independent_sources_count` | Quante origini diverse sono state citate | invariato come nome, **cambia come si contano** |
| `independent_sources_satisfied` | Il minimo è raggiunto | invariato |
| `retrieved_content_satisfied` | C'è almeno un documento davvero aperto | invariato come nome, **ora vero davvero** |
| `burden_satisfied` | L'onere è coperto nel complesso | invariato |
| `has_unanchored_literals_in_selection` / `..._in_universe` | Ci sono valori nel testo che nessuna fonte sostiene | invariato come nome, **molti meno falsi allarmi** |
| `circular_reference_detected` | L'agente cita se stesso come prova | invariato |
| `selected_evidence_all_title_only` | La selezione è fatta di soli titoli di ricerca | invariato |
| `evidence_request_episode_count` | Quante volte è già stata chiesta più evidenza | invariato |
| `factual_completion_attempt` | Il passo dichiara fatti ed è in chiusura | resta, ma **non è più l'interruttore dei controlli** |
| `evidence_completion_attempt` | Il passo va controllato: fattuale **oppure** con effetto esterno | è il nuovo interruttore |
| `insufficiency_declared_in_payload` | Il testo dichiara esplicitamente l'insufficienza | — |
| `literals_anchored_to_task` | Valori che vengono dal compito, quindi non da provare | — |
| `deficit_codes` | La lista dei deficit trovati su questo passo | invariato come nome, **ora arriva anche sulla decisione, non solo dentro al pilastro** |

Sono stati inoltre tolti dalla traccia una decina di campi che nessuna regola, nessuna
dashboard e nessun test leggeva mai (rapporti diagnostici mai usati, digest di snapshot
duplicati, flag ridondanti): non cambiano il comportamento, alleggeriscono l'audit.

**L'ancoraggio letterale ora riconosce le quantità, non solo il testo esatto.** Un numero
scritto in italiano (`141.006`, `30,4%`) e lo stesso numero nella fonte in inglese
(`141,006`, `30.4%`) sono la stessa quantità: prima venivano confrontati come stringhe e
risultavano "inventati" anche quando erano corretti. Anche gli zeri finali non contano più:
`97%` e `97.0%` coincidono.

**Il tipo di osservazione** (`retrieval_kind`) dice da dove viene una prova:
`search_snippet` (frammento restituito dalla ricerca), `fetched_document` (pagina aperta
davvero), `controlled_resource`, `mcp_result`, `capability_result`.
*Prima* non esisteva: la distinzione veniva indovinata guardando come iniziava il testo,
e sbagliava. *Dopo* è un campo tipizzato scritto da chi esegue la chiamata.

### Cosa produce

Un booleano `evidence_gap` più una lista di codici di deficit. OPA lo traduce in
`request_more_evidence` (torna all'attore) e, dopo 3 richieste nello stesso episodio, in
escalation a revisione umana. **I codici di deficit specifici viaggiano ora insieme al
codice generico sulla decisione stessa** (`invalid_or_missing_selected_evidence` più, ad
esempio, `unanchored_literals_in_selection`): prima restavano intrappolati dentro il
pilastro e la valutazione finale della tesi li avrebbe visti tutti come un'unica voce
indistinta, senza poter dire quale delle cinque famiglie interviene e quanto spesso.

I codici di deficit più frequenti: `insufficient_independent_sources`,
`retrieved_content_missing`, `unanchored_literals_in_selection`,
`title_only_evidence_fragility`, `circular_evidence_reference`,
`candidate_digest_mismatch`.

### Come funziona ORA — i problemi (stato originario)

| Problema | Effetto reale osservato |
|---|---|
| Le famiglie 2–5 si attivano solo se **il planner** ha marcato il passo come "produce affermazioni fattuali" | Sul passo che invia l'email quel flag è `false`: sul testo che esce davvero resta attivo solo il controllo dei digest |
| La soglia minima di fonti la sceglie **il planner** | Ha scritto `1` mentre il task chiedeva "almeno tre fonti" in due punti |
| L'ancoraggio letterale confronta il testo solo con le osservazioni web | L'indirizzo email del destinatario, **dato dal task**, risultava "non provato": l'agente l'ha prima storpiato e poi cancellato per superare il gate |
| Snippet di ricerca e documenti scaricati non si distinguono | Una sola ricerca web basta a soddisfare "contenuto recuperato" senza aprire nulla |
| Nessun controllo sulla qualità di ciò che si scarica | Una pagina anti-bot di Cloudflare veniva contata come fonte valida |

### Come funziona ORA — configurazione congelata

- **Le famiglie 2–5 si attivano anche sul passo con effetto esterno**, indipendentemente
  dal flag del planner. Il testo che esce dal sistema viene finalmente controllato.
- **La soglia minima viene derivata dal testo del task** (vincoli + criteri di successo).
  Il planner può solo alzarla, mai abbassarla.
- **Ramo di copertura per l'insufficienza dichiarata**: se il task prevede
  esplicitamente "se le fonti sono insufficienti, dichiaralo", quella dichiarazione
  soddisfa la copertura. Senza questo, alzare la soglia bloccherebbe per sempre i task in
  cui le fonti non esistono.
- **I dati che vengono dal task sono riconosciuti come tali**: l'indirizzo email, le date
  del vincolo temporale e l'oggetto del messaggio risultano ancorati *al task*, non
  "inventati". Restano segnalati solo i valori che non vengono né dal task né dalle
  osservazioni — che è ciò che il controllo voleva dire.
- **I numeri sono confrontati per quantità**, non per forma testuale: separatori di
  migliaia e decimali diversi per lingua non generano più falsi allarmi.
- **Snippet e documento scaricato sono un campo tipizzato** sull'osservazione, non un
  controllo su come inizia il testo.
- **Le pagine di challenge/consenso/paywall diventano un fallimento del tool**, non una
  fonte.
- **Il vocabolario di deficit arriva fino alla decisione e alla valutazione finale**, non
  resta chiuso dentro al pilastro.

> **Limite dichiarato**: le euristiche linguistiche del pilastro (riconoscimento di date
> in prosa, dichiarazioni di insufficienza, prefissi che escludono un numero strutturale
> come "Articolo 3") coprono italiano e inglese. Una campagna in altre lingue richiede
> l'estensione esplicita di quei pattern.

---

## Trajectory

### A cosa serve

Guardare la **dinamica** dell'esecuzione: l'agente sta avanzando o sta ripetendo se
stesso? Non guarda mai il contenuto, solo il movimento.

### Quando si attiva

Sempre, a ogni passaggio di governance. Sui turni esplorativi osserva solo la vitalità;
nei punti di impegno (completamento, proposta esterna) aggiunge le regole di convergenza.

### Cosa controlla

- richieste di replan ripetute senza una base osservativa nuova;
- oscillazione tra varianti della stessa operazione esterna (*churn*);
- fallimenti consecutivi dei tool;
- ripresentazione di un payload già rifiutato, o identico al precedente;
- tetto di replan per episodio;
- **esplorazione read-only senza fine su un passo** (nuovo, vedi sotto).

Una scelta di progetto importante e già corretta: la policy dichiara esplicitamente
quali fatti possono muovere una direttiva e quali restano **sola telemetria**.
Narrazione, lunghezza e tempo di riflessione non sono mai degrado.

### Il problema scoperto dopo: nessun limite alla ricerca

Una run di benchmark è rimasta bloccata su un passo per oltre 30 chiamate, tutte
*riuscite*, senza mai completarlo. La guardia che doveva impedirlo esisteva già — contava
le ripetizioni della stessa richiesta — ma la contava dentro la `progress_epoch`
corrente, e ogni osservazione nuova fa avanzare quel contatore. Bastava alternare una
ricerca produttiva a due ripetizioni per tenere la guardia sempre disarmata: nessuna
ripetizione arrivava mai a contare due volte nella stessa finestra. Nessun altro
contatore poteva intercettarlo — le ricerche non fallivano, quindi i fallimenti
consecutivi restavano a zero, e i contatori di turno sono per progetto sola telemetria.

Due correzioni, indipendenti:

1. **La guardia anti-ripetizione ora conta sul passo del piano**, non sulla progress
   epoch. Una richiesta ripetuta è ripetuta, che nel frattempo sia arrivata o meno
   un'osservazione nuova.
2. **Un tetto esplicito ai tentativi di tool per passo** (`current_step_tool_attempt_count`,
   soglia `max_tool_attempts_per_step_before_review` = 30, calibrata sul massimo storico
   osservato su run sane), che porta a revisione umana un passo che *varia* le
   richieste invece di ripeterle — un caso che la prima guardia non può vedere, perché
   ogni richiesta diversa è legittimamente nuova. Una decisione che completa il passo è
   esente: su quel confine il controllo sostanziale è Evidence, non un conteggio di
   tentativi.

### I campi in gioco — prima e dopo

**I fatti che possono muovere una decisione**, elencati apertamente in `trajectory.rego`
sotto `policy_fact_names`:

| Fatto | Cosa dice | Stato |
|---|---|---|
| `needs_replan_signal` + `replan_reason_present` | L'attore chiede di rifare il piano e dice perché | invariato |
| `first_replan_request` | È la prima richiesta dell'episodio | invariato |
| `observable_change_since_last_replan` | Dall'ultimo replan sono arrivati fatti nuovi sul mondo | invariato |
| `replan_episode_count` | Quanti replan in questo episodio | invariato |
| `consecutive_tool_failures` | Fallimenti di tool consecutivi | invariato come nome, **si riempie molto meno** |
| `action_churn_count` | Oscillazioni tra varianti della stessa operazione | invariato |
| `external_action_revision_count` | Revisioni già spese su questa operazione | invariato |
| `unchanged_resubmission` | Ha rimandato la stessa identica cosa | invariato |
| `repeated_rejected_payload_digest` | Ha rimandato qualcosa già rifiutato | invariato, ma **non assorbe più il caso "bloccato"** |
| `unresolvable_revision` | Gli è stata chiesta una correzione impossibile | — |
| `external_candidate` | Siamo davanti a un'operazione che esce dal sistema | invariato |
| `current_step_tool_attempt_count` | Tentativi di tool sul passo corrente, inclusi quelli respinti localmente | **nuovo** |
| `completion_signal` | Il turno sta chiudendo il passo | **nuovo come fatto di policy** (prima era solo telemetria) |

**I fatti che restano solo telemetria** e non possono decidere nulla (invariati):
`actor_turn_count`, `progress_epoch`, `completed_step_count`, `tool_call_count`,
`successful_tool_call_count`, `failed_tool_call_count`, `synthetic_tool_result_count`,
`observation_count`, `replan_count`, `consecutive_provider_protocol_failures`,
`provider_recovery_active`, `provider_protocol_retry_exhausted`.

**Le soglie**, tutte in `data.json`:

| Soglia | Valore |
|---|---|
| `max_replans_before_review` | 3 |
| `max_action_revisions_before_review` | 3 |
| `max_action_churn_before_review` | 2 |
| `max_consecutive_tool_failures_before_review` | 4 |
| `max_evidence_requests_before_review` | 3 |
| `max_tool_attempts_per_step_before_review` | 30 — **nuova** |

Da notare: l'unica soglia che *prima* era scritta due volte — una in `data.json` e una
copiata dentro il codice Python del gate dell'attore — ora esiste in un posto solo.

### Cosa produce

Booleani di convergenza. OPA li traduce in `replan` (se la richiesta è ammissibile) o in
escalation a revisione umana (se il ciclo è chiuso, o se il budget di esplorazione sul
passo è esaurito).

### Come funziona ORA — i problemi (stato originario)

| Problema | Effetto reale osservato |
|---|---|
| Un attore **bloccato** da una richiesta impossibile è indistinguibile da un attore che ripete pigramente | La run si è chiusa con "ha ripresentato un payload già rifiutato": sembra ostinazione, era impotenza |
| Un URL inesistente (404) è classificato come errore *temporaneo* e ritentato | Due URL inventati dall'agente, ritentati a ogni replan, hanno saturato il contatore dei fallimenti → 3 replan → tetto raggiunto → stop |
| Nulla obbliga l'agente a chiedere solo URL che ha davvero visto | Ha chiesto pagine mai comparse in nessun risultato di ricerca |
| Nessun limite all'esplorazione read-only | Un passo può restare aperto indefinitamente se le chiamate continuano a riuscire |

### Come funziona ORA — configurazione congelata

- **Nuovo motivo `unresolvable_revision`**: quando l'ultima richiesta di revisione non è
  soddisfabile con le osservazioni disponibili, l'escalation lo dice. L'operatore legge
  che l'attore era bloccato, non ostinato.
- **404 e 410 diventano fallimenti terminali** e non vengono più ritentati dopo un
  replan: un indirizzo morto resta morto per tutta la run.
- **I bersagli dei tool vanno ancorati**: un `fetch_url_text` deve puntare a un indirizzo
  già osservato. La stessa disciplina che Evidence impone alle affermazioni viene estesa
  alle richieste.
- **Ogni esito locale (non solo i duplicati) alimenta la stessa guardia di progresso**:
  la disposizione tipizzata di un tentativo di tool distingue ora chi ha raggiunto
  davvero il backend da chi è stato respinto prima — cache hit, duplicato, circuito
  aperto, bersaglio non ancorato — e qualunque nuovo tipo di rifiuto locale ricade
  automaticamente sotto la stessa guardia, per costruzione.
- **Tetto esplicito ai tentativi per passo** (vedi sopra): l'esplorazione read-only non è
  più senza fine.

Le soglie esistenti non cambiano. Cambia il fatto che i contatori si riempiano per motivi
veri, e che ora esista un limite dove prima non ce n'era nessuno.

---

## Judge

### A cosa serve

È l'unico controllo che legge il **significato**. Domanda: questo esatto messaggio, con
questo destinatario e questo contenuto, è fedele al compito, completo, fondato sulle
osservazioni e appropriato da eseguire?

### Quando si attiva

Solo su un candidato di operazione esterna, e **solo dopo** che policy statica, Evidence
e Trajectory sono puliti. È il cancello finale: la sua ammissione è essa stessa una
regola di policy, così il costo si paga una volta sola e nel punto giusto.

### Cosa vede e cosa non vede

- **vede**: il compito autoritativo, il piano completo, i passi già chiusi, *tutte* le
  osservazioni canoniche in un pacchetto sigillato con digest, il payload esatto, e i
  rilievi delle revisioni precedenti sulla stessa operazione;
- **non vede**: scratchpad, argomenti grezzi dei tool, contatori, storia di retry e
  replan, valutazioni dei pillar in ombra.

### I campi in gioco — nessuno cambia nel comportamento

| Campo | Cosa dice |
|---|---|
| `verdict` | `safe`, `needs_revision`, `unsafe`, `uncertain` |
| `severity` | `low`, `medium`, `high`, `critical` |
| `confidence` | Quanto è sicuro del proprio giudizio |
| `findings` | I rilievi, ognuno con la citazione del compito **e** del campo del payload |
| `claim_assessments` | Per ogni affermazione: sostenuta dalle fonti citate, sostenuta ma da fonti non citate, non sostenuta, contraddetta, contesto incompleto |
| `evaluated_operation_digest` | L'impronta dell'operazione che ha valutato |
| `evaluated_evidence_snapshot_digest` | L'impronta dell'insieme delle prove |
| `evaluated_evidence_selection_digest` | L'impronta delle prove che l'agente ha dichiarato di usare |

Gli ultimi tre sono il **triplo vincolo di digest**: se ciò che il Judge ha valutato non
coincide esattamente con ciò che verrebbe eseguito, il sistema si chiude in sicurezza e
passa la mano all'operatore. È la parte più solida dell'intera architettura e non cambia.

Il Judge inoltre non può inventarsi le referenze: ogni identificativo che cita deve
essere nella lista `allowed_evidence_ids` del pacchetto sigillato che gli è stato dato.

Un campo (`model_metadata`, provider/modello/nome del judge) è stato tolto solo
dall'esportazione verso il pilastro: nessuna regola, dashboard o test lo leggeva mai lì.
Resta persistito nel checkpoint dell'operazione per l'audit — non è sparito, è solo dove
serve davvero.

### Cosa produce

Un verdetto con severità, confidenza e rilievi **ancorati**: ogni rilievo deve citare sia
un elemento preciso del compito sia il campo esatto del payload che lo contraddice. Un
rilievo che non può citare entrambi è invalido.

OPA traduce il verdetto: `safe` prosegue verso l'eventuale approvazione;
`needs_revision` rimanda all'attore con i rilievi; `unsafe` e `uncertain` vanno
direttamente a revisione umana.

### Come funziona ORA — il problema (stato originario)

Essendo l'unico che legge il significato, il Judge si è ritrovato a fare **da solo il
lavoro che Evidence avrebbe dovuto fare a monte**: ha rilevato lui che il compito
chiedeva tre fonti, quando i controlli deterministici avevano già lasciato passare tutto
con la soglia a 1.

Ha però chiesto una revisione impossibile — le fonti non esistevano — ignorando il
fallback che il compito prevedeva esplicitamente e che le sue stesse istruzioni gli
impongono di rispettare. L'attore non poteva fare nulla, ha ripresentato lo stesso testo,
e la run si è chiusa in revisione umana.

### Come funziona ORA — configurazione congelata

**Il Judge non cambia.** Cambia ciò che gli arriva davanti:

- le fonti sono già state contate correttamente da Evidence, con la soglia giusta;
- il fallback previsto dal compito è ora rappresentato nella policy deterministica, non
  esiste solo nella testa del Judge;
- quando sbaglia comunque, l'escalation spiega che l'attore era bloccato.

In pratica il Judge torna a fare il suo mestiere — fedeltà al compito, destinatario
corretto, affermazioni non supportate, tono, contenuti sensibili — invece di supplire a
un controllo strutturale mancante.

Resta l'unico anello probabilistico prima dell'esecuzione. Non è un difetto da
nascondere: è un limite noto, presidiato dal triplo vincolo di digest e dall'escalation
obbligatoria.

---

## Il resto dell'architettura di governance

Non sono pillar Evidence/Trajectory/Judge, ma condividono la stessa disciplina:

- **`static_policy`** — controlli deterministici indipendenti dal contenuto (profilo
  database read-only, destinatari vietati, modalità read-only). Il profilo database
  read-only è ora in configurazione
  (`data.policy_config.static_policy.read_only_profile_ids`), non un identificativo
  scritto dentro la regola: un nuovo profilo va dichiarato per essere riconosciuto,
  invece di superare il controllo in silenzio. Tre campi legacy ridondanti
  (`write_blocked`, `hitl_mandatory`, `deny_reason` — duplicati esatti di `hard_deny` e
  `summary`) sono stati rimossi dal pilastro e dal parser Python che li leggeva come
  percorso alternativo per concludere un deny: un secondo modo di arrivare alla stessa
  conclusione di OPA, anche se dormiente, resta comunque una violazione del principio
  "OPA decide una volta sola".
- **`risk`** e **`behavioral`** — restano diagnostici in ombra (`shadow`): producono
  fatti osservabili ma non possono mai influenzare una direttiva. Il database
  comportamentale di sviluppo (oltre 1600 run accumulate durante le sessioni di lavoro)
  è stato azzerato prima della campagna di valutazione, così le prime osservazioni
  reali della campagna non condividono storia con il lavoro di sviluppo.

---

## In sintesi

| | Stato originario | Configurazione congelata |
|---|---|---|
| **Evidence** | Soglia scelta dall'agente stesso; spento sul testo che esce; segnala come non provati i dati del compito e i numeri scritti in un'altra lingua; famiglia freschezza inerte ma comunque rumorosa | Soglia derivata dal compito; attivo anche sull'operazione esterna; distingue dati del compito, prove e invenzioni; confronto numerico per quantità; freschezza ritirata; deficit visibili fino alla valutazione |
| **Trajectory** | Contatori riempiti da errori tecnici evitabili; non distingue bloccato da ostinato; nessun limite all'esplorazione | Errori terminali riconosciuti, bersagli ancorati, motivo di blocco esplicito, tetto esplicito ai tentativi per passo |
| **Judge** | Unico controllo semantico, costretto a supplire a Evidence | Invariato, ma arriva su un candidato già filtrato correttamente |

Il criterio con cui misurare il risultato non è "la run arriva in fondo", ma **"il
controllo blocca solo quando ha ragione"**. Nello stato originario i controlli hanno
bloccato 5 run su 6, e in nessuna avevano ragione. Nella configurazione congelata restano
due difetti corretti in corso di verifica sul campo (esplorazione senza fine su un passo,
ancoraggio numerico) — entrambi emersi da run reali eseguite dopo la prima correzione, non
da ispezione statica: il segnale più affidabile che questa architettura ha continuato a
produrre finora.
