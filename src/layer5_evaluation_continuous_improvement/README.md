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

Esempio operativo:

```text
python -m layer5_evaluation_continuous_improvement.main --database artifacts/evaluation/layer5.sqlite3 ingest artifacts/runtime --experiment-id baseline
python -m layer5_evaluation_continuous_improvement.main --database artifacts/evaluation/layer5.sqlite3 assess-run RUN_ID correct --method human
python -m layer5_evaluation_continuous_improvement.main --database artifacts/evaluation/layer5.sqlite3 assess-governance EVENT_ID yes
python -m layer5_evaluation_continuous_improvement.main --database artifacts/evaluation/layer5.sqlite3 assess-operation ROW_ID yes
python -m layer5_evaluation_continuous_improvement.main --database artifacts/evaluation/layer5.sqlite3 report --output artifacts/evaluation/baseline-report --experiment-id baseline
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
- timestamp della run.

Lo stato tecnico e il risultato del task restano distinti: una run può essere completata dal runtime ma produrre un risultato errato. Il trace v6 corrente non espone ancora timestamp completi della run, quindi `duration_seconds` resta `NULL` fino all'estensione di Layer 4.

### `governance_events`

Contiene una riga per ogni governance pass.

- run associata;
- direttiva e reason code;
- indicazione dell'effettiva richiesta di human review;
- valutazione successiva sulla necessità della review: `true`, `false` oppure `NULL`.

Confrontando review richiesta e review necessaria si ricavano escalation corrette, evitabili e mancate, oltre a precision e recall dell'oversight. Non viene quindi salvato un generico outcome complessivo dell'oversight.

### `human_reviews`

Contiene una riga per ogni decisione umana.

- run e governance event associati;
- decisione: `approve`, `edit`, `reject` o `abort`;
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

L'importer accetta esclusivamente `full-trace-2.json` con versione
`layer2_trace.v6`. Le directory legacy che contengono soltanto
`governance_conversation.*` o `governance_exchanges/` — inclusa la run 031 —
sono intenzionalmente escluse dal corpus corrente: quei debug exchange non sono
un trace canonico e non vengono ricostruiti retroattivamente. Per includere una
run legacy è necessario rieseguirla o produrre un artefatto v6 completo.

## Metriche e risultati

Il Layer 5 calcola nel primo slice:

- correct, partial e incorrect completion rate;
- completion tecnica rispetto alla completion corretta;
- abort e tool failure rate;
- durata media e mediana;
- actor turn, tool call e replanning per run;
- human intervention rate;
- escalation corrette, evitabili e mancate;
- precision e recall delle escalation;
- distribuzione di approve, edit, reject e abort;
- tempo di review;
- external operation proposte, modificate, respinte, eseguite e fallite;
- errori esterni intercettati ed errori sfuggiti al controllo;
- confronto tra esperimenti e configurazioni.

Le metriche vengono calcolate dai dati elementari tramite query e funzioni dedicate, senza salvare inizialmente aggregati ridondanti nel database.

## Plotting e reporting

Il primo slice genera barre per stato tecnico, task result, decisioni umane e stato delle external operation, oltre alla confusion matrix delle escalation. I grafici successivi previsti sono:

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
