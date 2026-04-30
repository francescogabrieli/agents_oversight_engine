# Architettura fino a qui

### Layer 1

Il primo layer normalizza il task e lo rende computabile dal runtime.

### Layer 2

Il secondo layer esegue il lavoro operativo: pianifica, agisce, osserva, ripianifica e sottopone le uscite a governance.

### Governance

La governance non è un layer esterno teorico. È un nodo reale del runtime che decide se continuare, ripianificare, chiedere review o fermare il run.

---

## 2. Panoramica generale

Il flusso generale oggi è questo:

```text
Task libero
  -> Layer 1 / Intake
  -> IntakeResult
  -> bootstrap_from_intake
  -> planner
  -> actor
  -> governance
  -> tool_gateway | replanner | finalize_bootstrap
  -> observe_tool_result
  -> actor
  -> ...
```

In pratica:

- Layer 1 prepara il task
- Layer 2 costruisce e aggiorna lo stato runtime
- l'actor produce un'azione locale o una richiesta di capability
- la governance verifica che il comportamento sia compatibile con policy e contesto
- i tool read-only vengono eseguiti solo se il ramo operativo lo consente
- il replanner può correggere la traiettoria
- il runtime termina quando raggiunge uno stato stabile e canonico

---

## 3. Layer 1: task intake

Layer 1 vive in `src/layer1_intake`.

Il suo compito è trasformare input libero in un payload canonico e governabile.

### Output principali

- `TaskSpec`
- `ExecutionPolicy`
- `IntakeMetadata`
- `IntakeResult`

### Cosa fa concretamente

1. legge il task grezzo
2. estrae una struttura canonica
3. identifica dominio, sensibilità, contesto e vincoli
4. risolve la policy esecutiva a partire da configurazione, non dal prompt
5. restituisce un risultato stabile che può inizializzare il runtime

### Perché è importante

Questo layer serve a spostare il più presto possibile fuori dal runtime live tutto ciò che non deve essere deciso in modo dinamico.

In altre parole:

- il task viene capito prima di essere eseguito
- la policy viene assegnata, non inventata
- il runtime riceve un oggetto già “pronto per l'azione”

### Regola architetturale

Layer 1 è esterno al grafo LangGraph.
Questa separazione è intenzionale: il runtime deve partire da uno stato già canonico, non da un input testuale grezzo.

---

## 4. Layer 2: runtime agentico

Layer 2 vive in `src/layer2_runtime_governance`.

È il cuore del sistema e contiene:

- stato runtime
- planner
- actor
- tool gateway
- osservazione dell'evidenza
- replanning
- governance

### Obiettivo del layer

Il layer deve prendere un task già canonico e portarlo avanti in modo controllato, con stati espliciti e senza nascondere le decisioni chiave dentro il prompt.

### File principali

- `state.py`
- `graph.py`
- `nodes.py`
- `planning.py`
- `acting.py`
- `replanning.py`
- `tool_contract.py`
- `actor_contract.py`
- `tool_gateway.py`
- `governance.py`

---

## 5. TaskState: il contratto centrale

`TaskState` è il centro del runtime.

È un dataclass che raccoglie tutto ciò che serve per sapere:

- cosa è stato pianificato
- cosa è stato eseguito
- cosa è stato osservato
- quali validazioni sono state prodotte
- quale ramo del workflow è attivo
- quali decisioni di governance sono state prese

### Campi concettualmente importanti

- stato corrente del runtime
- piano semantico corrente
- indice del passo attivo
- storia degli step eseguiti
- step validations
- scratchpad audit-safe
- evidenze osservate
- actor result
- tool request corrente
- tool execution result
- governance history
- last governance decision

### Perché è importante

`TaskState` evita che il runtime diventi una sequenza di variabili implicite sparse tra nodi.

Tutto ciò che conta resta:

- typed
- visibile
- serializzabile
- testabile

### Stati runtime rilevanti

Tra gli stati più importanti ci sono:

- `INITIALIZED`
- `READY_PLAN_EXECUTION`
- `READY_TOOL_EXECUTION`
- `WAITING_HUMAN_REVIEW`
- `ABORTED`
- `COMPLETED`

`WAITING_HUMAN_REVIEW` è oggi uno stop state tipizzato.
`ABORTED` rappresenta una denial hard e non va confuso con una review umana.

---

## 6. Planner: la traiettoria semantica

Il planner definisce la direzione del task.

### Cosa produce

Produce un `PlannerDecision` con una lista di `PlanStep`.

Ogni step ha:

- identificatore
- obiettivo
- razionale
- outcome atteso
- stato

### Cosa non fa

Il planner non deve:

- emettere tool call concrete
- produrre payload esecutivi
- decidere l'azione tecnica minuto per minuto

Il suo compito è semantico: dire *cosa va ottenuto*, non *come* farlo in dettaglio operativo.

### Collegamento con `docs/plan-and-execute.ipynb`

Il pattern è quello del planning semantico seguito da esecuzione step-bounded.
Questo è coerente con il riferimento locale del notebook.

---

## 7. Actor: esecuzione del passo corrente

L'actor lavora sul passo corrente del piano.

Non è un secondo planner.
Non rifà il task da zero.
Non riscrive la traiettoria globale.

### Input dell'actor

L'actor riceve un `ActorTask`, che include:

- task completo
- piano completo
- passo corrente
- eventuali passi precedenti
- evidenze già osservate
- scratchpad
- step validations
- contesto runtime

### Output dell'actor

L'actor produce un `ActorResult` tipizzato.

Gli esiti possibili includono, in sintesi:

- output locale/intermedio
- richiesta di tool
- richiesta di chiarimento
- richiesta di replanning
- output finale

### Perché il design è questo

L'actor è il punto dove si prende la decisione operativa locale, ma sempre dentro vincoli chiari.

Questo evita due problemi:

- actor troppo passivo, che ripete il piano senza agire
- actor troppo libero, che diventa un planner parallelo

### Scratchpad

L'actor scrive nel scratchpad in modo audit-safe.
Non si conserva chain-of-thought grezza.
Si conservano invece tracce compatte e leggibili.

---

## 8. Tool contract e tool gateway

Il tool system è separato dal resto del runtime.

### Contratti

`tool_contract.py` definisce:

- `ToolRequest`
- `ToolExecutionResult`
- `ObservedEvidence`

### Gateway

`tool_gateway.py` espone i tool effettivamente disponibili al runtime.

Nel progetto attuale il surface è read-only e conservativo.

### Principio chiave

I tool non vengono descritti solo nel prompt.
La loro disponibilità è modellata in modo strutturato, così il runtime può controllare davvero l'esecuzione.

### Perché questo è importante

Questo evita che il comportamento reale dipenda solo dalla comprensione testuale dell'LLM.

Il tool usage diventa:

- tipizzato
- verificabile
- osservabile
- governabile

---

## 9. Observability: evidenze, step history e validations

Il runtime non deve solo agire, ma anche spiegare cosa ha fatto e perché.

### Tre livelli fondamentali

#### 1. Step history

Registra l'ordine degli step e i nodi attraversati.

#### 2. Observed evidence

Registra l'evidenza concreta raccolta da tool o micro-azioni.

#### 3. Step validations

Valuta se un passo può essere considerato completato.

### Principio importante

Un `INTERMEDIATE_OUTPUT` non completa automaticamente il passo.

Per chiudere uno step serve una validation positiva e una quantità di evidenza sufficiente.

### Perché conta

Questo è uno dei punti più importanti del progetto: il sistema non “crede” automaticamente di aver finito solo perché ha prodotto testo.

Deve esserci un passaggio di verifica.

---

## 10. Governance: controllo tipizzato del comportamento

La governance è ora un nodo reale del grafo.

### File dedicato

`governance.py`

### Contratti principali

- `PolicyDecision`
- `GovernanceDirective`
- `GovernanceReasonCode`
- `PolicyEvaluationInput`
- `PolicyEvaluationResult`
- `GovernanceDecision`
- `PolicyEnginePort`

### Cosa entra nella policy evaluation

L'input della policy è compatto e policy-relevant.

Include, tra le altre cose:

- dominio del task
- label del task
- sensibilità
- policy esecutiva
- runtime flags
- stato runtime corrente
- step corrente
- tipo di risultato dell'actor
- capability mode
- external impact
- tool request compatta
- tool counters
- evidence count

### OPA come motore deterministico

La policy engine target è OPA via HTTP.
Il codice Python:

1. costruisce il documento di input
2. chiama OPA
3. valida la risposta
4. la mappa in una decisione tipizzata
5. fallisce in chiusura se la risposta è malformata

### Decisioni

Le decisioni principali sono:

- `ALLOW`
- `ALLOW_READ_ONLY`
- `ASK_CLARIFICATION`
- `REQUIRE_HUMAN_REVIEW`
- `DENY`

### Mapping runtime

La decisione policy viene convertita in una direttiva runtime:

- `ALLOW` -> `CONTINUE`
- `ALLOW_READ_ONLY` -> `CONTINUE_READ_ONLY`
- `ASK_CLARIFICATION` -> `ASK_USER_CLARIFICATION`
- `REQUIRE_HUMAN_REVIEW` -> `WAIT_HUMAN_REVIEW`
- `DENY` -> `STOP`

### Stato aggiunto

La governance ha introdotto:

- `last_governance_decision`
- `governance_history`
- `RuntimeState.ABORTED`

### Perché conta

La governance rende esplicito il controllo del runtime.
Non è più un effetto collaterale del prompt o di una condizione sparsa nel codice.

---

## 11. Replanning

Il replanning è un ramo reale del runtime.

### Quando interviene

Interviene quando l'actor segnala `NEEDS_REPLAN`.

### Cosa fa

Il replanner:

- riceve contesto di recovery
- legge i passi precedenti
- produce una nuova traiettoria
- sostituisce solo il tratto corrente e futuro

### Vincolo importante

Il replanner non modifica la storia già completata.

Questo mantiene immutabile il passato esecutivo.

### Perché è importante

Il sistema resta agentico, ma non perde controllo sulla traiettoria.

---

## 12. Graph wiring

Il grafo LangGraph è il punto in cui tutti i contratti si incontrano.

### Flusso attuale

```text
START
  -> bootstrap_from_intake
  -> planner
  -> actor
  -> governance
  -> tool_gateway | replanner | finalize_bootstrap
  -> observe_tool_result
  -> actor
  -> ...
```

### File

`graph.py`

### Cosa fa davvero

- istanzia i servizi
- collega i nodi
- definisce il routing condizionale
- mantiene il grafo testabile con dependency injection

### Cosa non deve fare

`graph.py` non deve contenere logica di business complessa.
Deve soltanto orchestrare e instradare.

---

## 13. `trace.py` e `main_layer2.py`: export osservabile

`src/main_layer2.py` è il punto migliore da cui debuggare l'intero sistema.

`src/layer2_runtime_governance/trace.py` ora contiene il contratto di trace riusabile.
Il modulo legge `TaskState` in modo read-only e produce un artefatto tipizzato, JSON-safe e auditabile.
`src/main_layer2.py` resta l'entrypoint operativo e stampa viste compatte o complete derivate dallo stesso exporter.

### Cosa mostra

- output di Layer 1
- stato iniziale del runtime
- trace completo Layer 2
- compact trace derivato dal trace tipizzato
- summary delle decisioni prese
- governance history
- oversight history
- tool calls ed evidenze osservate

### Perché è utile

Permette di vedere la pipeline completa senza dover aprire subito ogni singolo modulo.
Lo stesso contratto puo diventare base per payload HITL, evaluation harness e judge semantico.

### Cosa controllare quando si debuga

- `bootstrap_from_intake`
- `planner`
- `actor`
- `governance`
- `tool_gateway`
- `observe_tool_result`
- `replanner`

---

## 14. Stato attuale della repo

Ad oggi il progetto copre già in modo consistente:

- Intake canonico
- Runtime Layer 2
- Planner semantico
- Actor sul passo corrente
- Backend OpenRouter `gpt-oss-120b` per i primi test cloud dell'actor
- Tool loop read-only
- Observability
- Replanning
- Governance tipizzata
- Oversight deterministico `confidence_risk_trajectory`
- Runtime trace exporter tipizzato
- Test deterministici

### Cosa è stabile

- Layer 1 produce un `IntakeResult` canonico
- Layer 2 ha un `TaskState` strutturato
- l'actor è separato dal planner
- il tool usage è tipizzato
- la governance è reale e tracciata
- OPA e oversight vengono combinati senza rendere la policy piÃ¹ permissiva
- il trace Layer 2 Ã¨ esportabile come JSON stabile
- i test sono verdi

### Cosa non è ancora attivo

Nota sui modelli: nei primi test cloud l'actor usa `gpt-oss-120b` via OpenRouter.
Questa resta una scelta temporanea per test gratuiti/intermedi.
Quando il runtime sara stabile, l'actor verra migrato verso un grande modello cloud stabile.
Planner e replanner restano separati dall'actor e possono mantenere backend diversi.

Non sono ancora parte del runtime attivo:

- LLM-as-a-judge
- HITL completo con `interrupt / resume`
- persistenza automatica dei trace su file o backend osservabilitÃ 

### Significato architetturale

Questo è importante: il progetto non è “finito”, ma è già in uno stato coerente.
I confini principali sono stati fissati.
Le estensioni future potranno innestarsi senza dover rifare il modello base.

---

## 15. Verifica e test

La stabilità non è lasciata all'impressione manuale.

### Suite attuale

La suite è verde e copre:

- Layer 1
- integrazione Layer 1 -> Layer 2
- planner
- actor
- tool loop
- replanning
- governance
- mapping policy
- deny path
- clarification path
- read-only path

### Significato

I test servono a garantire che:

- i contratti restino stabili
- il grafo non si rompa
- il runtime continui a comportarsi in modo prevedibile

---

## 16. Come raccontarlo ai tutor

La sintesi corretta è questa:

1. il task viene prima canonizzato
2. il runtime prende in carico il task come stato strutturato
3. il planner genera una traiettoria semantica
4. l'actor esegue il passo corrente
5. la governance controlla la compatibilità dell'azione
6. i tool read-only e le evidenze rientrano nel ciclo
7. il replanner corregge la traiettoria se necessario
8. il sistema termina in uno stato canonico e tracciabile

### Frase breve utile

> Abbiamo costruito un runtime agentico governato, con intake canonico, esecuzione controllata, osservabilità e un punto di controllo esplicito già attivo nel grafo.

---

## 17. Prossimo passo naturale

Il passo successivo non è “aggiungere altro caos”, ma innestare in modo ordinato:

- confidence
- risk
- trajectory scoring
- LLM-as-a-judge
- HITL con `interrupt / resume`

L'architettura attuale è il livello di base su cui questi strati potranno essere aggiunti senza perdere chiarezza.
