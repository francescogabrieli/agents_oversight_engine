# Layer 5 — Evaluation & Continuous Improvement

## Scopo attuale

Il Layer 5 è un componente offline, separato dal ciclo live dell'agente. In questa fase copre soltanto la parte di **Evaluation**; il feedback loop per il continuous improvement sarà progettato successivamente.

L'obiettivo è raccogliere i risultati delle run, valutarne efficacia, efficienza e qualità dell'oversight, quindi produrre dati e grafici riproducibili per la tesi.

## Implementazione

Il package pubblico è `layer5_evaluation_continuous_improvement`. I principali punti di estensione sono:

- `store.py`: schema SQLite e aggiornamento delle valutazioni;
- `ingestion.py`: scoperta, validazione e proiezione dei trace v6;
- `metrics.py`: metriche aggregate;
- `reporting.py`: esportazione JSON, CSV e grafici SVG;
- `main.py`: CLI offline.

Non importa modelli del runtime live e usa soltanto la libreria standard Python.

Il runner `scripts/run_evaluation_campaign.py` aggiorna automaticamente il database
`artifacts/evaluation/layer5_evaluation.sqlite3` (personalizzabile con `--database`),
usando il `campaign_id` come identificativo dell'esperimento. Registra l'intero piano
prima di lanciare il primo comando e sincronizza il database prima e dopo ogni run.
`--dry-run` non crea né artifact né database.

Esempio operativo (con `src` nel percorso Python; dalla root è utilizzabile anche
`python -m src.layer5_evaluation_continuous_improvement.main`):

```text
python -m layer5_evaluation_continuous_improvement.main assess-run RUN_ID correct --method human
python -m layer5_evaluation_continuous_improvement.main assess-governance EVENT_ID yes
python -m layer5_evaluation_continuous_improvement.main assess-operation ROW_ID yes
python -m layer5_evaluation_continuous_improvement.main report --output artifacts/evaluation/baseline-report --experiment-id CAMPAIGN_ID
```

## Flusso generale

1. Layer 4 esporta il trace JSON canonico della run.
2. L'importer del Layer 5 valida il trace e ne calcola il digest.
3. I dati utili vengono normalizzati e salvati nel database di evaluation.
4. Eventuali valutazioni automatiche o umane completano i dati sulla correttezza.
5. Query dedicate calcolano metriche per run, task ed esperimento.
6. Il reporting produce tabelle, grafici e dataset utilizzabili nella tesi.

L'importazione deve essere idempotente: lo stesso trace non può generare run duplicate. Il trace originale rimane la fonte autorevole; il database conserva il suo percorso e digest senza copiarne indiscriminatamente tutto il contenuto.

## Database

Per il proof of concept viene utilizzato un database SQLite dedicato, distinto da checkpoint LangGraph, runtime artifact store, review inbox e behavioural memory.

### `experiments`

Raggruppa le run appartenenti alla stessa campagna sperimentale.

- identificatore, nome e descrizione;
- data di creazione.

### `runs`

Contiene una riga per ogni esecuzione.

- esperimento, task case e configurazione utilizzata;
- percorso e digest del trace;
- stato tecnico: `completed`, `aborted` o `waiting`;
- causa di terminazione, quando presente (`governance_denied`, `human_abort`,
  `external_execution_failed`, `runtime_failure` o altra causa tipizzata);
- risultato del task: `correct`, `partial`, `incorrect` oppure `NULL` se non valutato;
- metodo di valutazione: automatico, umano oppure assente;
- durata, actor turn, tool call, tool failure e replanning;
- identità dello scenario (`scenario_id`, `scenario_family`, `replica_index`,
  `campaign_id`), quando la run proviene da una campagna;
- timestamp della run.

Lo stato tecnico e il risultato del task restano distinti: una run può essere completata dal runtime ma produrre un risultato errato. Il trace v6 non espone timestamp di run: `scenario_id`, `scenario_family`, `replica_index`, `campaign_id` e `duration_seconds` vengono letti dal `run_record.json` che il runner di campagna scrive accanto al trace, non dal trace stesso. Una run importata da una directory priva di quel file (ad esempio ingerita direttamente da `artifacts/runtime/` invece che da `artifacts/evaluation/input/...`) ha questi campi a `NULL` e finisce nella famiglia "non dichiarata" del breakdown.

Il runner avvia per ogni scenario la CLI completa `src/main_layer2.py`, non la funzione
one-shot del runtime. Il processo resta proprietario del thread durante
`waiting_human_review`, attende la decisione dalla dashboard e termina soltanto dopo la
continuazione o l'arresto definitivo. Ogni run pianificata produce sempre un
`run_record.json`: se il processo fallisce prima del trace, o una run viene esplicitamente
saltata, Layer 5 importa comunque il record con `trace_available = 0`. `total_runs`
rappresenta quindi il piano completo; i tassi operativi usano le run tentate e riportano
separatamente run saltate, non iniziate e copertura dei trace.

Le run successive a un `--fail-fast` o a un'interruzione restano `not_started`:
`total_runs = attempted_runs + skipped_runs + not_started_runs`. Un Ctrl+C durante
il comando registra `interrupted`. Una terminazione forzata che impedisce il cleanup
lascia il record già salvato come `started` (stato tecnico incompleto), senza perdere
il resto del piano. Per recuperare la proiezione si può eseguire `ingest` sulla sola
directory `artifacts/evaluation/input/realistic_pool_v1/CAMPAIGN_ID`, con
`--experiment-id CAMPAIGN_ID`. Un trace danneggiato viene segnalato come errore, ma
il record della run viene comunque conservato; non viene inventata una trace valida.

### `governance_events`

Contiene una riga per ogni governance pass.

- run associata;
- direttiva e reason code;
- indicazione dell'effettiva richiesta di human review;
- valutazione successiva sulla necessità della review: `true`, `false` oppure `NULL`.

Confrontando review richiesta e review necessaria si ricavano escalation corrette, evitabili e mancate, oltre a precision e recall dell'oversight. Non viene quindi salvato un generico outcome complessivo dell'oversight.

**Limite del protocollo di etichettatura.** `human_review_was_needed` viene annotato a mano, ed è naturale etichettare solo i turni che sono davvero finiti in escalation — sono quelli che si notano. Ma se si etichettano solo quelli, `false_negative` resta 0 per costruzione e la recall risulta un perfetto 1.0 anche senza avere mai verificato se un turno proseguito senza review avrebbe dovuto fermarsi. Precision e recall sono significative solo se il campione etichettato include anche una parte di eventi che *non* hanno richiesto review; il report riporta sempre quanti eventi sono etichettati sul totale, proprio perché il numero da solo non lo dice.

### `human_reviews`

Contiene una riga per ogni decisione umana.

- run e governance event associati;
- tipo di decisione tipizzata restituita dall'operatore (`approve_continue`, `approve_continue_read_only`, `modify_and_continue`, `request_more_evidence`, `replan`, `ask_clarification`, `abort`);
- tempo di review;
- feedback e identificativo del reviewer.

### `external_operations`

Contiene le azioni esterne proposte o eseguite.

- run, capability e payload digest;
- eventuale modifica umana;
- stato dell'esecuzione;
- correttezza dell'operazione: `true`, `false` oppure `NULL`.

Permette di distinguere operazioni corrette, errori intercettati prima dell'esecuzione ed errori realmente eseguiti.

## Valutazione della correttezza

Per il realistic pool, fissare e usare la [rubric per scenario](../../docs/evaluation/realistic_pool_v1_rubric.md)
prima della campagna. Distingue risultato del task, intervento umano e correttezza della governance.

`completed` non implica `correct`. Il risultato del task può essere determinato tramite:

- test automatico;
- confronto con il risultato atteso di un benchmark;
- rubric di valutazione;
- giudizio umano quando la verifica automatica non è possibile.

Le run non ancora giudicate mantengono `task_result = NULL` e non vengono incluse nel denominatore delle metriche di correttezza.

La classificazione `termination_reason` resta distinta dal risultato semantico
del task: un `governance_denied` è un arresto corretto del controllo, mentre un
`runtime_failure` è un fallimento tecnico da escludere dalle conclusioni sulla
qualità della policy.

### Confine del corpus

L'importer accetta i `full-trace-2.json` in versione `layer2_trace.v6` e, per le
campagne, anche i `run_record.json` privi di un trace fratello valido. File di debug o exchange
privi sia di trace sia di record di campagna restano esclusi. Le run di sviluppo che non
appartengono a una campagna versionata non vanno importate nel corpus. L'archivio
pre-evaluation è in `artifacts/runtime_pre_evaluation/`; il runner importa soltanto
la propria directory di campagna, non l'intero albero runtime.

## Metriche e risultati

Il Layer 5 calcola nel primo slice:

- correct, partial e incorrect completion rate;
- completion tecnica rispetto alla completion corretta;
- abort e tool failure rate;
- durata media;
- actor turn, tool call e replanning per run;
- human intervention rate;
- completamento tecnico e intervento umano per famiglia di task (il pool realistico copre dieci famiglie apposta perché questo confronto sia possibile, anche se alcune restano a una sola scenario e quindi a tre sole repliche);
- escalation corrette, evitabili e mancate;
- precision e recall delle escalation;
- distribuzione di approve, edit, reject e abort;
- tempo di review;
- external operation proposte, modificate, respinte, eseguite e fallite;
- errori esterni intercettati ed errori sfuggiti al controllo;
- confronto tra esperimenti e configurazioni.

Le metriche vengono calcolate dai dati elementari tramite query e funzioni dedicate, senza salvare inizialmente aggregati ridondanti nel database.

Un denominatore vuoto produce `None`/`null`, visualizzato come `N/D`, non uno zero.
Uno zero misurato resta invece zero. Precision, recall e F1 sono calcolati dal modulo
canonico delle metriche. Nella confusion matrix le righe indicano la necessità della
review e le colonne la richiesta effettiva: la prima riga è `TP, FN`, la seconda `FP, TN`.

## Plotting e reporting

Il primo slice genera barre per stato tecnico, task result, decisioni umane, stato delle external operation, distribuzione per pilastro di governance, medie di efficienza e completamento/intervento per famiglia di task, oltre alla confusion matrix delle escalation con la copertura dell'etichettatura in didascalia. I grafici successivi previsti sono:

- barre impilate degli outcome delle run;
- confronto tra completion tecnica e correttezza reale;
- confusion matrix delle escalation;
- interventi umani e decisioni per reason code;
- funnel `escalation → decisione umana → risultato`;
- funnel delle external operation;
- box plot di durata, turni, tool call e replanning;
- confronto tra esperimenti o configurazioni.

Il livello di query prepara dataset coerenti; il livello di plotting si limita a rappresentarli e non ridefinisce le metriche. Il primo slice produce SVG senza dipendenze esterne, insieme a CSV, manifest e identificativo dell'esperimento. PNG e PDF potranno essere derivati successivamente per l'impaginazione finale della tesi.

## Confini

- Il Layer 5 non modifica il runtime e non partecipa alle decisioni live.
- Layer 4 rimane proprietario dei trace e degli artifact originali.
- Il database di evaluation è una proiezione analitica, non una nuova fonte di verità.
- Il feedback loop e l'aggiornamento di soglie, prompt o policy non fanno parte di questa prima fase.
