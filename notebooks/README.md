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

1. **Esecuzione delle Run**: Il runtime live di Layer 2 genera i trace canonici `layer2_trace.v6` in `artifacts/runtime/<thread_id>/full-trace-2.json`.
2. **Ingestione in SQLite**:
   ```bash
   python -m layer5_evaluation_continuous_improvement.main --database artifacts/evaluation/layer5_evaluation.sqlite3 ingest artifacts/runtime --experiment-id baseline_experiment
   ```
3. **(Opzionale) Assegnazione Ground Truth**:
   ```bash
   python -m layer5_evaluation_continuous_improvement.main --database artifacts/evaluation/layer5_evaluation.sqlite3 assess-run <RUN_ID> correct --method human
   python -m layer5_evaluation_continuous_improvement.main --database artifacts/evaluation/layer5_evaluation.sqlite3 assess-governance <EVENT_ID> yes
   ```
4. **Esplorazione e Generazione Figure**:
   - Apri ed esegui `evaluation_analysis.ipynb` con Jupyter Notebook o in VS Code / Cursor / IDE.
   - Le figure verranno salvate automaticamente in `figures/thesis/` in formato `.pdf` e `.png`.
