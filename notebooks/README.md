# Notebooks di Valutazione per la Tesi

Questa cartella contiene i notebook interattivi e gli script di visualizzazione per l'elaborazione dei dati sperimentali e la generazione dei grafici per la Tesi di Laurea Magistrale:
**"Human Oversight and Human-in-the-Loop Control Engine for Autonomous AI Agents"**.

---

## Contenuto

- **`evaluation_analysis.ipynb`**: Notebook Jupyter interattivo che carica i dati da `artifacts/evaluation/layer5_evaluation.sqlite3`, calcola le metriche aggregate e visualizza i grafici di valutazione.
- **`thesis_plotting.py`**: Modulo con le routine di plotting (Matplotlib & Seaborn) configurate con stile accademico publication-ready e funzione di export in formato vettoriale `.pdf` (per LaTeX `\includegraphics`) e `.png` (300 DPI). Non ricalcola alcuna metrica: legge sempre `EvaluationSummary` prodotto da `layer5_evaluation_continuous_improvement.metrics.build_evaluation_summary`, la stessa funzione che alimenta il bundle SVG di `reporting.py`. Richiede un database di evaluation già popolato da una ingestion reale: non genera più dati sintetici di fallback.

## Installazione

Le dipendenze di questa pipeline (`pandas`, `matplotlib`, `seaborn`, `ipykernel`) sono separate da
`requirements-production.txt`/`requirements-dev.txt` perché servono solo per l'esportazione delle
figure della tesi, non per il runtime o i test:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-evaluation.txt
```

---

## Flusso Operativo Completo (Layer 2 → Layer 4 → Layer 5 → Tesi)

Per sessioni da quattro task, il runner accetta `--scenario-start 1 --scenario-end 4`
(estremi inclusi), poi `5–8`, `9–12`, `13–16`. Ogni sessione usa un `--campaign-id`
distinto, ad esempio `realistic_eval_20260910_v1_b01` fino a `_b04`, e `--replicas 1`.
Il manifest completo resta conservato; vengono preregistrate soltanto le run selezionate.
Tutti i blocchi confluiscono nello stesso database. Per aggregarli nel report omettere
`--experiment-id` usando un database dedicato a questo giro: i blocchi sono sessioni
operative, non condizioni sperimentali differenti. Un ID già esistente non viene ripreso.

1. **Esecuzione delle Run**: Il runner `scripts/run_evaluation_campaign.py` lancia la CLI live e raccoglie trace e record in `artifacts/evaluation/input/realistic_pool_v1/<campaign_id>/<thread_id>/`. Registra in SQLite anche le run fallite, saltate e non iniziate.
2. **Ingestione in SQLite**: Automatica prima e dopo ogni run, nel database `artifacts/evaluation/layer5_evaluation.sqlite3`. Solo per recupero o reimportazione offline della campagna:
   ```bash
   python -m src.layer5_evaluation_continuous_improvement.main ingest artifacts/evaluation/input/realistic_pool_v1/CAMPAIGN_ID --experiment-id CAMPAIGN_ID
   ```
3. **Assegnazione Ground Truth**: Necessaria per misurare correttezza semantica e qualità delle escalation. Senza annotazioni le metriche non disponibili sono `N/D`, non zero. Annotare anche eventi senza review per valutare le escalation mancate.
   ```bash
   python -m layer5_evaluation_continuous_improvement.main --database artifacts/evaluation/layer5_evaluation.sqlite3 assess-run <RUN_ID> correct --method human
   python -m layer5_evaluation_continuous_improvement.main --database artifacts/evaluation/layer5_evaluation.sqlite3 assess-governance <EVENT_ID> yes
   ```
4. **Esplorazione e Generazione Figure**:
   - Apri ed esegui `evaluation_analysis.ipynb` con Jupyter Notebook o in VS Code / Cursor / IDE.
   - Le figure verranno salvate automaticamente in `figures/thesis/` in formato `.pdf` e `.png`.
