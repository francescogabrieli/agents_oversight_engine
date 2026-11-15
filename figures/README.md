# Figure per il capitolo 3

Le immagini in questa directory sono le versioni selezionate per la tesi. Le
sorgenti SVG delle tavole editoriali sono mantenute accanto alle PNG; le sorgenti
Mermaid operative restano nei documenti originali.

## Selezione consigliata

| File | Ruolo nel capitolo 3 | Uso consigliato |
| --- | --- | --- |
| `architecture_solution_clean.png` | Vista architetturale semplificata della soluzione | Figura principale dell'architettura |
| `layer2_runtime_graph_thesis.png` | Topologia verticale del runtime LangGraph | Figura del ciclo operativo Layer 2 |
| `opa_first_governance_clean.png` | Pipeline OPA-first, pillar attivi e Judge | Figura del meccanismo di governance |
| `evidence_violation_vs_concern_clean.png` | Deficit correggibile e Concern di ancoraggio: remediation limitata o review umana | Approfondimento dei gate Evidence |
| `trajectory_progress_vs_stall_clean.png` | Richieste successive di replan con o senza nuova base osservabile | Approfondimento di Trajectory Health |
| `hitl_external_clean.png` | Proposta esterna, autorizzazione HITL, esecuzione e riconciliazione | Figura del controllo delle azioni esterne |
| `hitl_async_resume_sequence.png` | Sequenza asincrona tra runtime CLI, checkpoint, snapshot, dashboard, revisore e inbox | Figura del confine `interrupt / resume` e dell'ownership della run |
| `evidence_trace_clean.png` | Tool read-only, osservazione concreta, stato, governance e trace | Figura dell'auditabilità |
| `candidate_binding_clean.png` | Binding tra proposta, snapshot e selezione delle evidenze; invalidazione al cambio di versione | Figura dell'identità del candidato valutato |

## Figure opzionali o da appendice

| File | Motivazione |
| --- | --- |
| `layer2_runtime_graph.png` | Esportazione completa con tutte le transizioni LangGraph; utile come riferimento tecnico |
| `minimal_safe_intervention_clean.png` | Versione grafica della tabella degli interventi; nel corpo del capitolo è preferibile la tabella testuale |
| `archive/` | Vecchie esportazioni Mermaid/Excalidraw e riferimento generico plan-and-execute; conservate per tracciabilità, non per il corpo del capitolo |

La figura `hitl_async_resume_sequence` ha una sorgente SVG modificabile accanto
al PNG. Descrive il trasporto della decisione:
lo snapshot serve alla dashboard, l'inbox consegna il comando e soltanto il processo
CLI owner riprende il checkpoint canonico.

Le figure sperimentali già presenti nelle sottocartelle `figures/thesis/` sono da
collocare nel capitolo di valutazione, non nel capitolo 3.

`candidate_binding_clean.svg` è il sorgente modificabile della nuova figura.
Le frecce rappresentano il binding all'identità condivisa, non l'ordine di
invocazione dei controlli. Assessment e approvazioni storiche restano auditabili,
ma non sono trasferibili alla nuova versione. La PNG in `images/` è una copia
identica fornita per il percorso alternativo richiesto; il sorgente canonico
resta in `figures/`.

Le due nuove figure di approfondimento e la tabella Minimal Safe Intervention
hanno sorgenti SVG modificabili accanto alle PNG a 300 dpi (3200 pixel di
larghezza). Le esportazioni PDF vettoriali sono in `output/pdf/`, con gli stessi
nomi base. Etichette, didascalie suggerite, tabella copiabile e fonti di verifica
sono raccolte in [`chapter3_supporting_figures.md`](chapter3_supporting_figures.md).
Si tratta di esempi concettuali dei gate correnti, non di traiettorie sperimentali
o misure della minimalità degli interventi.
