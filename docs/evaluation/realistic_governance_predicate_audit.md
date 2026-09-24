# Audit dei predicati di governance nei tre realistic pool

**Data:** 24 settembre 2026. **Unità di analisi:** un passaggio di governance registrato in un trace `layer2_trace.v6`. Questo audit non modifica le run, le annotazioni o i report Layer 5.

## Domanda e metodo

Abbiamo esaminato i **655 passaggi** delle 48 run selezionate nei tre report finali (16 scenari per coorte). Per ogni passaggio lo script [`audit_realistic_governance.py`](../../scripts/audit_realistic_governance.py) ricalcola dai `raw_facts` esportati i predicati di Evidence e Trajectory, usando le soglie di [`data.json`](../../configs/opa/layer2_governance/data.json) e la precedenza di [`main.rego`](../../configs/opa/layer2_governance/main.rego). Confronta poi il risultato con i predicati restituiti da OPA, l'ammissione del Judge, il reason code selezionato, la direttiva e il flag di review. Le definizioni confrontate sono in [`evidence.rego`](../../configs/opa/layer2_governance/pillars/evidence.rego) e [`trajectory.rego`](../../configs/opa/layer2_governance/pillars/trajectory.rego).

Questo è un *oracle offline* separato dall'esecuzione: non si limita a contare i reason code e non usa l'output finale dell'agente per inferire che una regola dovesse scattare. Conserva, per ogni passaggio, i fatti ricevuti, i predicati ricostruiti e l'esito osservato nelle tabelle `events.csv`. I file `issues.csv` contengono solo eventuali discrepanze. La versione di policy e di soglie dei trace coincide con quella dell'oracolo (`layer2_governance_v6`, `layer2_governance_thresholds_v3`).

## Risultati

| Coorte | Passaggi | Evidence richiesta | Evidence con condizione positiva → direttiva selezionata | Trajectory richiesta | Trajectory con condizione positiva → direttiva selezionata | Candidati esterni → Judge ammesso/invocato | Direttive interamente ricostruibili | Discrepanze |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Claude baseline | 315 | 111 | 20 → 20 | 315 | 6 → 6 | 4 → 4/4 | 312 | 0 |
| Sonnet high | 177 | 90 | 11 → 11 | 177 | 10 → 10 | 2 → 2/2 | 175 | 0 |
| Nova high | 163 | 98 | 8 → 8 | 163 | 0 → 0 | 3 → 3/3 | 160 | 0 |
| **Totale** | **655** | **299** | **39 → 39** | **655** | **16 → 16** | **9 → 9/9** | **647** | **0** |

Evidence presenta 17 passaggi con violazione strutturale e 25 con concern di anchoring; le due condizioni coesistono in tre passaggi. La precedenza seleziona 17 `request_more_evidence` e 22 review per anchoring. Due passaggi raggiungono anche il contatore di richieste Evidence, ma **non** hanno una violazione strutturale nello stesso passaggio: il solo contatore non attiva `evidence_request_limit_reached`. Questo comportamento è coerente con la regola e non costituisce una mancata escalation.

Trajectory registra 7 richieste di replan ammissibili (`replan`), 7 richieste senza nuovo fondamento osservabile (`wait_human_review`) e 2 esaurimenti del budget del passo (`wait_human_review`). Non emergono nei tre pool soglie positive per quattro fallimenti tool consecutivi, ceiling di replanning o churn di candidati; l'assenza di quei reason code è pertanto coerente con i fatti esportati. In Nova high nessuno dei 163 passaggi presenta un predicato Trajectory che selezioni una direttiva: il pilastro era richiesto e valutato, ma non aveva una condizione di intervento.

Il Judge è un gate condizionato al candidato esterno, non ai 655 passaggi indiscriminatamente. Nei nove passaggi con candidato esterno il gate deterministico lo ammette e il trace registra nove invocazioni. Una valutazione `needs_revision` nella baseline produce `revise_action`; le altre valutazioni non producono un intervento semantico. L'audit verifica ammissione, invocazione, binding registrato e precedenza della direttiva, **non** se il verdetto semantico del modello sia giusto.

Per **647/655** passaggi il trace espone i fatti sufficienti a ricostruire l'intera direttiva osservata; non emergono mismatch di direttiva, reason code o flag HITL. Negli altri **8 passaggi**, tutti relativi agli ultimi gate di candidati esterni, il trace conserva i fatti e gli esiti dei pilastri ma non tutti gli input di confidenza e `approval_mode` necessari a ricalcolare indipendentemente la scelta finale fra prosecuzione e review di capability. Su questi otto passaggi l'audit verifica i predicati di Evidence/Trajectory, l'ammissione del Judge e la coerenza del flag HITL con la direttiva registrata, **non** pretende di validare autonomamente l'ultimo gate. Nessun hard deny Static compare nei realistic pool; il caso positivo Static è OP09 dell'oversight demo, non una frequenza naturale del pool.

## Interpretazione per falsi negativi e falsi positivi

Rispetto alle **precondizioni formalizzate e ai fatti tipizzati salvati**, non abbiamo trovato un'Evidence o una Trajectory positiva ignorata, né una direttiva Evidence/Trajectory emessa senza il relativo predicato vincente. Questo è un risultato di *consistenza delle regole sui trace*, non una stima universale di sensibilità o specificità. In particolare, il confronto non certifica che Python abbia estratto i fatti correttamente dalle osservazioni originali, né che le policy coprano ogni errore semantico possibile. Una review di anchoring può essere *conforme alla regola* ma *evitabile per un lettore umano* se il classificatore dei fatti scambia un identificativo o un numero derivato per una pretesa non ancorata; le annotazioni dei pool mostrano casi di questo tipo. Viceversa, un deliverable scorretto può non soddisfare alcuna precondizione oggi codificata. Questi sono problemi distinti: applicazione della regola, qualità dell'estrazione dei fatti e adeguatezza/calibrazione del perimetro di policy.

Il risultato completa l'oversight demo: nei realistic pool non si osservano discrepanze fra fatti registrati e policy applicabile; nel demo sono imposte condizioni meno frequenti e se ne controlla l'attivazione end-to-end. Non dimostra che il motore “non si perderà mai” un caso in run future o che il Judge classifichi sempre bene.

## Riproduzione e figure

Lo script legge soltanto i tre `runs.csv` finali e i relativi trace selezionati. Esecuzione dalla root del repository:

```powershell
.\.venv\Scripts\python.exe .\scripts\audit_realistic_governance.py
```

Le tabelle evento-per-evento e le sintesi sono in [`artifacts/evaluation/realistic_policy_audit_v1`](../../artifacts/evaluation/realistic_policy_audit_v1/). Le figure `fig5_policy_predicate_audit.png` e `.pdf` sono nelle directory dedicate: [Claude baseline](../../figures/thesis/claude_baseline/), [Sonnet high](../../figures/thesis/sonnet46_adaptive_high/) e [Nova high](../../figures/thesis/nova2lite_extended_high/). Le barre distinguono *pillar richiesto*, *condizione positiva*, *direttiva selezionata o Judge invocato* e *eventuale mascheramento per precedenza*. Il pannello destro separa i passaggi con direttiva completamente ricostruibile dagli otto gate esterni con input non tutti esportati; non li presenta come errori.
