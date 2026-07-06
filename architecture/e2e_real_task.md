# E2E Task Reale

Questo documento descrive il flusso end-to-end reale del repository su un task concreto, usando come esempio:

`Find official sources about the Digital Markets Act and produce a short summary.`

L'obiettivo non e descrivere un'architettura ideale o futura.
L'obiettivo e mostrare che cosa fa oggi il codice, in quale ordine, e con quali artifact osservabili.

## 1. Ingresso

1. `src/main_layer2.py` riceve `--task` e opzionalmente `--context`.
2. `src/layer1_intake` converte il testo libero in un `IntakeResult` tipizzato.
3. Layer 1 assegna `ExecutionPolicy` da configurazione, non dal giudizio libero del modello.

Output rilevante:
- `TaskSpec`
- `ExecutionPolicy`
- `IntakeMetadata`
- `IntakeResult`

## 2. Bootstrap Runtime

1. `bootstrap_from_intake` costruisce `TaskState`.
2. I fatti autorevoli gia presenti nell'intake vengono materializzati come `observed_evidence` iniziale con provenance `intake_provided`.
3. Il thread runtime viene agganciato alla persistenza locale, cosi resume, dashboard e trace leggono la stessa run.

Effetto pratico:
- il runtime puo gia fare drafting locale prudente senza aver ancora usato tool
- la trace parte con una base esplicita di evidenza, non da stato vuoto

## 3. Planning Del Task Reale

Per un task "fonti ufficiali + short summary", il planner non dovrebbe produrre una pipeline lunga e generica.
La baseline attuale compatta i casi over-decomposed verso una traiettoria piu proporzionata.

Pattern tipico:
1. raccogliere e verificare fonti ufficiali
2. produrre un summary breve al livello di precisione supportato

Ogni `PlanStep` puo includere:
- `step_kind`
- `evidence_requirement`
- `claim_precision_ceiling`

Questo evita che l'actor debba inferire tutta la governance dai soli testi naturali del piano.

## 4. Actor, Governance E Tool Loop

Il loop reale e questo:

```text
bootstrap_from_intake
  -> planner
  -> actor
  -> governance
  -> { tool_gateway | replanner | human_review | finalize_bootstrap }
tool_gateway
  -> observe_tool_result
  -> actor
```

Per il task DMA, il comportamento atteso e:

1. l'actor lavora solo sullo step corrente
2. se servono fonti ufficiali, emette una `ToolRequest` read-only
3. `governance` valuta sempre il turn prima del tool
4. solo dopo una direttiva compatibile il `tool_gateway` esegue davvero il tool
5. `observe_tool_result` converte il risultato in `ObservedEvidence` con support level esplicito
6. l'actor riparte con quella evidenza osservata

Tool tipici in questo scenario:
- `web_search`
- `fetch_url_text`

## 5. Come Decide La Governance Oggi

La governance attuale non e piu "OPA + massimo livello di conservativita".
Lavora in strati espliciti:

1. `static_policy`
2. `dynamic_policy`
3. `behavioral`
4. `judge`
5. `decision_combiner`

Dettagli rilevanti:
- `static_policy` e `dynamic_policy` vengono valutati in parallelo nel nodo `governance`
- `deny`, `require_human_review` e `ask_clarification` di OPA restano veto hard
- `behavioral` produce similarity, deviation e novelty score su memoria SQLite locale
- il `judge` e selettivo e puo restare `disabled` mantenendo il contratto typed
- il `decision_combiner` usa escalation pesata: i layer advisory non possono scavalcare da soli la static policy
- se il combiner propone `wait_human_review` ma la governance normalizza il turn intermedio verso `continue` o `request_more_evidence`, la trace conserva sia la direttiva originale del combiner sia la normalizzazione post-combiner
- il caso `allow_read_only` mantiene visibile il vincolo read-only anche quando la direttiva finale diventa piu conservativa di `continue_read_only`

Effetto pratico sul task reale:
- se l'evidenza raccolta supporta solo un summary alto livello, il runtime deve abbassare la precisione del testo
- non deve escalare automaticamente a human review solo per un singolo segnale advisory debole
- deve invece fermarsi o chiedere review quando il blocco hard OPA o l'escalation pesata lo richiedono davvero

## 6. External Communication

Se il task produce una comunicazione esterna o un draft reviewabile:

1. il runtime costruisce il draft nello stato `external_communication`
2. la governance distingue tra draft parziale e draft review-ready
3. solo il draft governato review-ready apre il percorso HITL
4. l'operatore puo approvare, rifiutare, chiedere piu evidenza o usare `modify_and_continue`

Per un semplice task di ricerca con summary breve, questo ramo puo non attivarsi.
Ma il contratto resta gia parte della baseline reale.

## 7. HITL Reale

L'HITL oggi non dipende da Studio.
Dipende da:

- `interrupt / resume` LangGraph
- checkpoint locali
- artifact runtime persistiti
- dashboard standalone `src/main_layer2_dashboard.py`

Artifact chiave:
- `artifacts/runtime/checkpoints/layer2_checkpoints.json`
- `artifacts/runtime/<thread_id>/trace.json`
- `artifacts/runtime/<thread_id>/runtime_summary.json`
- `artifacts/runtime/<thread_id>/pending_human_review.json`

## 8. Uscita E Valutazione

A fine run:

1. `trace.py` esporta la trace tipizzata dal solo `TaskState`
2. la governance trace espone anche il motivo della normalizzazione post-combiner quando la direttiva finale differisce da quella scelta dal combiner
3. `persistence.py` aggiorna artifact per dashboard e replay
4. `behavioral.py` persiste il read model storico locale
5. `src/main_layer2_evaluation.py` puo leggere la trace offline senza dipendere dal runtime live

Questo chiude il ciclo e2e reale:

`task libero -> intake canonico -> runtime governato -> evidenze tipizzate -> eventuale HITL -> trace esportabile -> evaluation offline`
