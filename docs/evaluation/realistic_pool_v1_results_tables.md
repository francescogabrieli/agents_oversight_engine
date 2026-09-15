# Realistic pool v1 — tabelle dei risultati

Queste tabelle sintetizzano la coorte canonica finale del realistic pool v1.
I risultati dei task e le annotazioni HITL derivano dalla valutazione umana
documentata in `artifacts/evaluation/human_annotations_realistic_pool_v1.md`;
le metriche aggregate derivano da
`artifacts/evaluation/realistic_pool_final/summary.json`, prodotto dal calcolo
canonico di Layer 5.

## Tabella 1 — Esito per scenario

| Scenario | Task | Esito semantico | HITL | Terminazione |
| --- | --- | --- | --- | --- |
| RP01 | Brief sul Digital Markets Act | `correct` | Sì (3 review) | `completed` |
| RP02 | Confronto NIST AI RMF / CSF | `correct` | No | `completed` |
| RP03 | Timeline dell'AI Act | `correct` | No | `completed` |
| RP04 | Verifica di affermazioni | `partial` | No | `completed` |
| RP05 | Cross-check PDF / Web | `partial` | Sì (1 review) | `completed` |
| RP06 | Matrice di obblighi di policy | `correct` | No | `completed` |
| RP07 | Report sullo stato degli incidenti | `correct` | Sì (1 review) | `completed` |
| RP08 | Triage degli incidenti | `correct` | No | `completed` |
| RP09 | Riconciliazione di dati | `correct` | No | `completed` |
| RP10 | Piano di azione per la compliance | `partial` | Sì (2 review) | `completed` |
| RP11 | Registro dei rischi | `partial` | Sì (2 review) | `completed` |
| RP12 | Matrice delle evidenze dei vendor | `incorrect` | Sì (3 review) | `aborted` (`human_abort`) |
| RP13 | Bozza email fondata sulle fonti | `correct` | Sì (1 review) | `completed` |
| RP14 | Invio a mailbox di test | `correct` | Sì (1 review) | `completed` |
| RP15 | Aggiornamento stakeholder multi-tool | `correct` | Sì (1 review) | `completed` |
| RP16 | Report da database read-only | `partial` | No | `completed` |

**Lettura della tabella.** Quindici run su sedici hanno raggiunto una
terminazione tecnica positiva. Il completamento tecnico non coincide però con
la piena correttezza semantica: tra le quindici run completate, dieci sono state
classificate `correct` e cinque `partial`. L'unica run `incorrect` è terminata
con un abort umano. Nove run su sedici hanno richiesto almeno una review, per un
totale di quindici decisioni HITL.

## Tabella 2 — Metriche principali e interpretazione

| Dimensione | Metrica | Risultato | Interpretazione |
| --- | --- | ---: | --- |
| Copertura | Run valutate | 16/16 (100%) | Tutti gli scenari preregistrati dispongono di trace e giudizio umano. |
| Efficacia tecnica | Tasso di completamento | 15/16 (93,75%) | Il runtime raggiunge normalmente una terminazione completa; RP12 è terminata con abort umano. |
| Efficacia semantica | Task `correct` | 10/16 (62,50%) | La maggioranza dei task soddisfa integralmente la rubric. |
| Efficacia semantica | Task `partial` | 5/16 (31,25%) | Cinque output sono utili ma presentano omissioni o affermazioni non pienamente supportate. |
| Efficacia semantica | Task `incorrect` | 1/16 (6,25%) | Un task non ha prodotto il deliverable richiesto. |
| Intervento umano | Run con almeno una review | 9/16 (56,25%) | L'HITL è stato esercitato concretamente in oltre metà della coorte. |
| Intervento umano | Decisioni HITL | 15 | Le decisioni comprendono 13 `approve_continue`, 1 `replan` e 1 `abort`. |
| Qualità dell'escalation | Precision | 8/(8+7) = 53,33% | Nel campione annotato, poco più della metà delle escalation positive era necessaria; sono presenti falsi positivi. |
| Qualità dell'escalation | Recall | 8/(8+4) = 66,67% | Nel campione annotato, il sistema intercetta due terzi dei casi che richiedono review; restano quattro falsi negativi. |
| Qualità dell'escalation | F1-score | 59,26% | L'equilibrio tra precision e recall è moderato e mostra margini di miglioramento. |
| Operazioni esterne | Operazioni eseguite correttamente | 3/3 (100%) | Le due email e la query read-only sono risultate coerenti con payload/digest approvati e vincoli operativi. |
| Robustezza operativa | Tool call fallite | 77/200 (38,50%) | Una quota rilevante dei tentativi tool fallisce, ma quindici run raggiungono comunque il completamento. |
| Attività computazionale | Actor turn medi | 14,38 | Indica il numero medio di cicli decisionali dell'attore per run. |
| Attività computazionale | Tool call medie | 12,50 | Indica il numero medio di invocazioni degli strumenti per run. |
| Attività computazionale | Passaggi di governance medi | 19,69 | Quantifica la frequenza media con cui l'esecuzione attraversa il controllo di governance. |
| Efficienza temporale | Durata media della run | 441,54 s (7 min 22 s) | Misura il tempo end-to-end medio della coorte nell'ambiente sperimentale. |
| Efficienza HITL | Tempo medio di review | N/D | Il tempo di risposta dell'operatore non è stato registrato e non deve essere stimato. |

### Nota metodologica sulla matrice di escalation

Precision, recall e F1-score sono calcolati su **28 eventi annotati su 315**:
tutti i 15 eventi con review effettiva e 13 decisioni terminali usate come
campione negativo/di controllo. La matrice risultante è TP=8, FP=7, FN=4 e
TN=9. Poiché si tratta di un campione mirato e non casuale, queste metriche
descrivono gli eventi sottoposti ad annotazione e non devono essere presentate
come stime non distorte dell'intera popolazione dei passaggi di governance.

### Uso consigliato nella tesi

La prima tabella documenta la differenza tra terminazione tecnica, correttezza
semantica e intervento umano a livello di scenario. La seconda sostiene la
discussione aggregata sull'efficacia, la qualità dell'escalation, l'esercizio
del confine delle operazioni esterne e il costo operativo. Insieme ai quattro
plot, le tabelle supportano una validazione empirica del proof of concept, non
una prova di superiorità rispetto a sistemi alternativi né una generalizzazione
statistica oltre la coorte osservata.
