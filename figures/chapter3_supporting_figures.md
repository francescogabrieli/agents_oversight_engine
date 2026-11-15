# Figure di approfondimento per il capitolo 3

Queste tavole integrano la vista OPA-first con due dettagli del controllo di
oversight. Gli esempi sono illustrativi: non rappresentano run sperimentali,
frequenze di intervento o una dimostrazione dell'ottimalità delle direttive.
Il runtime e le policy non sono stati modificati.

## Formati

| Tavola | PNG, 300 dpi | Sorgente SVG | PDF vettoriale |
| --- | --- | --- | --- |
| Evidence Violation vs Evidence Concern | [PNG](evidence_violation_vs_concern_clean.png) | [SVG](evidence_violation_vs_concern_clean.svg) | [PDF](pdf/evidence_violation_vs_concern_clean.pdf) |
| Trajectory Health: progresso e stallo | [PNG](trajectory_progress_vs_stall_clean.png) | [SVG](trajectory_progress_vs_stall_clean.svg) | [PDF](pdf/trajectory_progress_vs_stall_clean.pdf) |
| Minimal Safe Intervention, tabella | [PNG](minimal_safe_intervention_clean.png) | [SVG](minimal_safe_intervention_clean.svg) | [PDF](pdf/minimal_safe_intervention_clean.pdf) |

La figura Evidence misura 3200 × 1360 pixel; la figura Trajectory e la tabella
misurano 3200 × 1480 pixel. Per l'impaginazione è preferibile il PDF vettoriale, che rimane
nitido a ogni scala. Le PNG servono per editor che richiedono immagini raster.
Gli SVG sono i sorgenti grafici canonici; i PDF e le PNG sono loro esportazioni.
La tabella testuale sotto è una trascrizione editoriale intenzionale da copiare
nel documento, non una seconda definizione di policy.

Le tavole privilegiano pochi elementi: due esiti per Evidence, due timeline per
Trajectory e cinque righe per la tabella. Condizioni, eccezioni e spiegazioni
restano nelle didascalie sotto, da accompagnare alle immagini nella tesi.

## Evidence Violation vs Evidence Concern

**Collocazione:** dopo la spiegazione di Evidence Integrity & Provenance e della
distinzione tra deficit strutturali e problemi di ancoraggio.

**Didascalia suggerita:** Distinzione dei percorsi di intervento per i gate
Evidence. Un deficit correggibile, illustrato da un URL citato ma non recuperato,
attiva una richiesta limitata di ulteriore evidenza. Una Concern di ancoraggio,
illustrata da un valore numerico non presente nel materiale disponibile, richiede
review umana: l'assenza di ancoraggio non distingue un valore derivato da un valore
inventato. Evidence produce fatti strutturali e OPA sceglie la direttiva. Se le
classi coesistono, vengono trattati prima i deficit correggibili.
Al limite delle richieste di ulteriore evidenza, OPA richiede review umana anche
per una Violation ancora aperta. I percorsi illustrati presuppongono l'assenza
di gate prioritari e, per la Concern, un problema non già adjudicated sul binding
corrente.

**Confini rappresentati:** le due classi formano l'unione storica `evidence_gap`,
che blocca completamento e ammissione del Judge. Il percorso è mostrato in assenza
di gate OPA prioritari. Una review di ancoraggio non conferisce autorizzazione a
eseguire un effetto esterno; tale autorizzazione appartiene al gate reason-scoped
`capability_approval_required`, legato all'esatto candidato/digest.

## Trajectory Health: progresso e stallo

**Collocazione:** dopo la spiegazione dell'ammissibilità delle richieste di replan
e dei limiti di convergenza.

**Didascalia suggerita:** Confronto illustrativo tra due richieste successive di
replan. Nel primo caso, una nuova osservazione costituisce una nuova base per una
richiesta motivata. Nel secondo, esiti locali senza invocazione del backend non
introducono una nuova base e la richiesta ripetuta conduce a review umana per
`replan_request_stalled`. La sola riscrittura del piano non rende ammissibile una
successiva richiesta di replan.
La prima richiesta motivata resta ammissibile anche senza una nuova base;
le timeline mostrano richieste successive, sotto il ceiling di replanning
e in assenza di condizioni prioritarie.

**Confini rappresentati:** la prima richiesta motivata è ammissibile anche senza
un cambiamento precedente. Per richieste successive, una nuova base comprende
un'invocazione che raggiunge il backend, una nuova osservazione oppure uno step
completato. Né `progress_epoch`, né la firma del nuovo piano, né una review umana
soddisfano da soli questo guard. Una nuova base non prova il successo del task.
Il ceiling sulle traiettorie sostituite nell'episodio è un controllo distinto,
che può richiedere review anche in presenza di una nuova base. Le timeline si
collocano sotto quel ceiling e in assenza di altre condizioni prioritarie.

## Minimal Safe Intervention: tabella copiabile

**Collocazione:** nella sezione che presenta il principio progettuale di
intervento proporzionato. La tabella testuale è adatta al corpo del capitolo;
la versione grafica può essere usata nelle slide.

| Problema e condizione | Esempio | Intervento mirato | Direttiva OPA |
| --- | --- | --- | --- |
| Deficit strutturale delle evidenze, entro il limite di remediation | URL citato ma non recuperato | Correggere o integrare l'evidenza e rivalutare | `request_more_evidence` |
| Proposta esterna con Judge `needs_revision` e revisione ancora ammessa | Finding semantico che richiede una correzione del payload | Revisionare il payload; nuovo digest e nuova valutazione | `revise_action` |
| Richiesta motivata di replan ammissibile, sotto il ceiling | Prima richiesta oppure richiesta successiva con nuova base osservabile | Sostituire la traiettoria pendente, preservando gli step completati | `replan` |
| Concern di ancoraggio residua, non già adjudicated sul binding corrente | Valore non ancorato al materiale disponibile | Review umana circoscritta alla ragione del gate | `wait_human_review` |
| Violazione statica con `hard_deny` | Proposta mutating in modalità read-only | Fermare la run prima dell'effetto; connector non invocato | `stop` |

**Nota da accompagnare alla tabella:** Le righe illustrano la risposta al singolo
gate in assenza di condizioni prioritarie. OPA/Rego mantiene l'autorità esclusiva
sulla direttiva e sui limiti. Minimal Safe Intervention è un principio
progettuale; questa tabella non ne dimostra l'ottimalità sperimentale.

## Fonti di verifica

- [Runtime contracts](../docs/agent-reference/runtime-contracts.md): classi Evidence,
  nuova base osservabile, review reason-scoped e authorization binding.
- [Evidence policy](../configs/opa/layer2_governance/pillars/evidence.rego):
  `evidence_violation`, `evidence_concern`, `evidence_gap` e ceiling delle richieste.
- [Trajectory policy](../configs/opa/layer2_governance/pillars/trajectory.rego):
  `replan_request_admissible`, `replan_request_stalled` e ceiling di replanning.
- [OPA decision policy](../configs/opa/layer2_governance/main.rego): precedenze,
  `judge_admission` e direttive finali.

## Esportazione

Le esportazioni sono state prodotte dai sorgenti SVG con CairoSVG, con scala 2
per le PNG e metadata di risoluzione a 300 dpi. I PDF sono vettoriali; le pagine
sono state renderizzate con MuPDF per la verifica visiva. Gli strumenti di
esportazione sono stati installati soltanto in una directory temporanea, senza
modificare le dipendenze del progetto.
