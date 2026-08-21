# Sequenza verificata di una richiesta governata

Il diagramma completo [`governed_request_sequence.mmd`](governed_request_sequence.mmd) resta disponibile come riferimento tecnico. Per la scrittura della tesi, il percorso è stato suddiviso in figure più piccole, ciascuna con una responsabilità esplicita.

## 1. Control loop principale

Questa figura mostra il ciclo operativo Layer 2 e le sole rotte principali dopo la governance.

```mermaid
flowchart LR
    intake["Task Intake<br/>canonical task"] --> planner["Planner<br/>semantic plan"]
    planner --> actor["Actor<br/>current step"]
    actor --> governance{"Governance<br/>OPA directive"}

    governance -->|continue| actor
    governance -->|tool request| gateway["Tool Gateway<br/>read-only capability"]
    gateway --> observation["Evidence Observation<br/>concrete result"]
    observation --> actor

    governance -->|replan| replanner["Replanner<br/>semantic trajectory"]
    replanner --> actor

    governance -->|human review| hitl["Human Review<br/>typed decision"]
    hitl -->|revision or evidence| actor
    hitl -->|replan| replanner
    hitl -->|approved external action| external["External Execution<br/>digest-bound proposal"]
    governance -->|external action| external
    external -->|executed| finalize["Finalize<br/>trace and checkpoint"]
    external -->|indeterminate| hitl
    governance -->|stop| finalize

    classDef core fill:#e8f0fe,stroke:#3b5ccc,color:#111
    classDef control fill:#fff3cd,stroke:#a87900,color:#111
    classDef human fill:#fce8e6,stroke:#c5221f,color:#111
    classDef terminal fill:#e6f4ea,stroke:#188038,color:#111
    class intake,planner,actor,gateway,observation,replanner core
    class governance control
    class hitl,external human
    class finalize terminal
```

Sorgente: [`governed_request_overview.mmd`](governed_request_overview.mmd).

## 2. Decisione di governance OPA-first

Questa figura isola il contributo centrale della tesi: i segnali attivi arrivano alla decisione Rego, mentre risk e behavioral restano shadow-only.

```mermaid
flowchart TD
    base["PEP<br/>base facts and static envelope"] --> requirements{"OPA / Rego<br/>required pillars"}

    requirements -->|evidence required| evidence["Evidence facts<br/>typed snapshot and selection"]
    requirements -->|trajectory required| trajectory["Trajectory facts<br/>progress and deviation"]

    base --> preliminary["OPA / Rego<br/>preliminary decision"]
    evidence --> preliminary
    trajectory --> preliminary
    preliminary --> admission{"Judge admission"}
    admission -->|required| judge["Semantic Judge<br/>digest-bound assessment"]
    admission -->|not required| final["OPA / Rego<br/>final directive"]
    judge --> final
    final --> route["PEP<br/>apply validated directive"]

    base -.->|external candidate| risk["Operational risk<br/>shadow-only facts"]
    base -.->|stable audit boundary| behavioral["Behavioral model<br/>shadow-only facts"]
    risk -.-> shadow["Shadow observations<br/>never influence directive"]
    behavioral -.-> shadow

    classDef active fill:#e8f0fe,stroke:#3b5ccc,color:#111
    classDef decision fill:#fff3cd,stroke:#a87900,color:#111
    classDef shadow fill:#f1f3f4,stroke:#80868b,color:#444
    classDef result fill:#e6f4ea,stroke:#188038,color:#111
    class base,evidence,trajectory,judge active
    class requirements,preliminary,admission decision
    class risk,behavioral,shadow shadow
    class final,route result
```

Sorgente: [`governance_decision_flow.mmd`](governance_decision_flow.mmd).

## 3. External operation e Human-in-the-Loop

Questa figura usa una sequenza soltanto per il percorso che beneficia davvero dell’ordine temporale: proposta, valutazione, intervento umano, esecuzione e riconciliazione.

```mermaid
sequenceDiagram
    autonumber
    participant A as Actor
    participant P as Governance PEP
    participant O as OPA / Rego
    participant J as Semantic Judge
    participant H as Human
    participant X as External Executor
    participant T as Trace

    A->>P: Governed external candidate + selected evidence
    P->>O: Preliminary input + operation and evidence digests
    O-->>P: Judge admission

    opt Judge required
        P->>J: Digest-bound candidate + evidence bundle
        J-->>P: Assessment, findings and digest bindings
    end

    P->>O: Final decision input
    O-->>P: Directive and reason codes

    alt Human review required
        P->>H: Typed request with allowed decisions
        H-->>P: Approve, edit, request evidence, replan or abort
        alt Approved candidate
            P->>X: Authorized digest-bound execution
        else Edited candidate or replan
            P-->>A: Revision returns to governance
        else Abort
            P->>T: Terminal review record
        end
    else Policy allows execution
        P->>X: Authorized digest-bound execution
    end

    opt Execution started
        X-->>P: Executed or indeterminate receipt
        alt Executed
            P->>T: External receipt and completion trace
        else Indeterminate
            P->>H: Reconciliation request
            H-->>P: Typed reconciliation decision
            P->>T: Reconciliation and receipt trace
        end
    end
```

Sorgente: [`external_operation_hitl_sequence.mmd`](external_operation_hitl_sequence.mmd).

## 4. Evidence e auditabilità

Questa figura mostra come una capacità read-only diventa osservazione concreta e come lo stato alimenta sia la governance sia il trace.

```mermaid
flowchart LR
    request["Actor tool request"] --> gateway["Tool Gateway<br/>read-only adapter"]
    gateway --> result["Tool execution result"]
    result --> observation["Concrete observation<br/>content and optional locator"]
    observation --> state["TaskState<br/>evidence, scratchpad and trajectory"]
    state --> governance["Governance<br/>active evidence and trajectory facts"]
    state --> trace["Trace builder<br/>step and governance records"]
    trace --> artifacts["Checkpoint and<br/>runtime artifacts"]

    classDef runtime fill:#e8f0fe,stroke:#3b5ccc,color:#111
    classDef evidence fill:#e6f4ea,stroke:#188038,color:#111
    classDef audit fill:#f3e8fd,stroke:#7b1fa2,color:#111
    class request,gateway,result runtime
    class observation,state,governance evidence
    class trace,artifacts audit
```

Sorgente: [`evidence_audit_flow.mmd`](evidence_audit_flow.mmd).
## Verifica del percorso

1. `build_runtime_graph` collega `START → bootstrap_from_intake → planner → actor`; `route_after_actor` restituisce sempre `governance`. Le rotte dopo governance sono `actor`, `tool_gateway`, `replanner`, `human_review`, `external_execution` oppure `finalize_bootstrap`.

2. Il PEP (`runtime/governance_service.py`) delega a `evaluate_opa_first_governance`. Prima costruisce fatti base, quindi interroga `required_pillars` in OPA. L’attivazione Rego è:

   - `INTERMEDIATE_OUTPUT`: solo `trajectory` per la liveness;
   - `PROPOSE_ACTION` senza contenuto fattuale: solo `trajectory` per la liveness;
   - `PROPOSE_ACTION` con contenuto fattuale: `evidence` e `trajectory`;
   - `NEEDS_REPLAN`: `trajectory` per liveness e convergenza;
   - `STEP_COMPLETED` locale: `evidence` e `trajectory`;
   - candidate `governed_external` non hard-denied: `evidence` e `trajectory`, poi Judge se l'ammissione OPA lo richiede.

3. `risk` viene raccolto quando esiste una proposta esterna, ma è shadow-only. `behavioral` è raccolto a una frontiera di audit stabile (`waiting_human_review`/`completed`) o quando esiste una proposta esterna; è anch’esso shadow-only. Entrambi vengono inclusi nel payload, ma Rego dichiara esplicitamente che non influenzano la direttiva.

4. OPA riceve tre richieste nel caso normale: requisiti dei pillar, decisione preliminare per leggere solo `judge_admission`, decisione finale. Il judge è chiamato soltanto se l’ammissione OPA è `required=true`; deve essere un candidato esterno governato, con digest dell’operazione e digest di snapshot/selezione delle evidenze coerenti.

5. Il “Decision Combiner” non è un microservizio o una classe Python separata: è il ruolo concettuale svolto dalla precedenza Rego nel bundle OPA. Il PEP valida e applica l’output, senza ricombinare autonomamente i segnali.

6. La final directive può essere `continue`, `continue_read_only`, `request_more_evidence`, `revise_action`, `replan`, `wait_human_review`, `ask_user_clarification` oppure `stop`. Gli errori in una chiamata OPA o nella validazione del relativo contratto producono un risultato fail-closed `wait_human_review`.

7. `observe_tool_result` conserva soltanto osservazioni concrete dell’adapter (`observation_id`, contenuto, locator facoltativo), aggiorna scratchpad/progress quando trova contenuto nuovo e poi ritorna all’actor oppure, con tool request pendente, alla governance.

8. La richiesta HITL viene costruita soltanto quando il PEP porta lo stato a `WAITING_HUMAN_REVIEW`; usa `interrupt / resume`, una request ID stabile, decisioni consentite e, per proposte esterne, un payload digest-bound. Una modifica umana rigenera la proposta e ritorna obbligatoriamente a governance.

9. Per `execute_after_governance`, una proposta esterna autorizzata passa anche da `external_execution`: preflight su digest, policy envelope, registro, allowlist e idempotenza. Soltanto una ricevuta `executed` completa automaticamente il task; una ricevuta `indeterminate` richiede riconciliazione HITL.

## Trace scritto

`RuntimeArtifactStore.persist` scrive per thread:

- `full-trace-2.json` (`layer2_trace.v6`): intake, piano, ultimo actor turn, execution/scratchpad/validazioni, tool call e osservazioni, tutti i `governance_passes` (requisiti, risultati/errori dei pillar, direttiva, ragione e invarianti), HITL, comunicazione, esecuzione esterna e `termination_reason`;
- `trace_summary.md`: vista Markdown del trace;
- `dashboard_snapshot.json` e `runtime_summary.json`;
- quando applicabile, il bundle di evidenze del judge, sia content-addressed sia come puntatore corrente.

Il trace viene materializzato dal wrapper `main_layer2.py` dopo il ritorno del grafo e durante la gestione dell’interruzione/ripresa; gli aggiornamenti di stato LangGraph sono invece checkpointed tramite lo SQLite saver.

## Punti non determinabili con certezza dal codice ispezionato

- Non è possibile dedurre dal codice una frequenza temporale universale di persistenza del trace: `RuntimeArtifactStore.persist` è invocato dai wrapper e dai percorsi di gestione, non direttamente da ogni nodo del grafo. Il checkpoint LangGraph è il meccanismo durevole tra le transizioni.
- L’ordine interno con cui OPA valuta i moduli Rego dei pillar non è osservabile come una sequenza di chiamate di processo: i moduli sono valutati nel singolo decision endpoint. Il diagramma usa “Decision Combiner (Rego)” come separazione concettuale, non come hop di rete.
- Il diagramma descrive il flusso standard con un `ActorResult`. Se `governance()` viene invocata senza `actor_result`, il servizio registra che la governance è stata saltata; il grafo normale non dovrebbe arrivarci dopo l’actor.

## Riferimenti verificati

- `src/layer2_runtime_governance/graph.py`
- `src/layer2_runtime_governance/runtime/routing.py`
- `src/layer2_runtime_governance/runtime/governance_service.py`
- `src/layer2_runtime_governance/governance_components/opa_pipeline.py`
- `src/layer2_runtime_governance/governance_components/policy_engine.py`
- `configs/opa/layer2_governance/main.rego`
- `src/layer2_runtime_governance/runtime/tool_service.py`
- `src/layer2_runtime_governance/hitl/__init__.py`
- `src/layer2_runtime_governance/runtime/external_execution_service.py`
- `src/layer4_state_evidence_observability/observability/builder.py`
- `src/layer4_state_evidence_observability/infrastructure/runtime_artifacts.py`
