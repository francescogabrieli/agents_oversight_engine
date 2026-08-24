# Analisi dell'architettura implementata

## Metodo e perimetro

Questo documento ricostruisce il comportamento dal codice e dalle configurazioni attive, non dai README. I riferimenti `file — simbolo` sono le evidenze sorgente. Non contiene diagrammi.

L'architettura effettiva è una pipeline a cinque macro-blocchi: Task Intake (Layer 1), Runtime & Governance (Layer 2), Tool Bus / External Capabilities (Layer 3), State / Evidence / Observability (Layer 4) ed Evaluation offline. I primi quattro sono layer di ownership che collaborano attraverso contratti tipizzati; Evaluation è un blocco post-run separato. L'autorità sull'azione di governance è OPA/Rego; Python costruisce i fatti, valida il contratto di trasporto e applica la direttiva restituita.

## Layer ownership

| Layer / blocco | Responsabilità | Confine |
|---|---|---|
| Layer 1 — Task Intake | Parsing canonico, policy e metadati | Fuori dal grafo live |
| Layer 2 — Runtime & Governance | Orchestrazione, planner/actor, governance, HITL e routing | Non implementa direttamente capability o persistenza |
| Layer 3 — Tool Bus / External Capabilities | Gateway, normalizzazione, registry, executor e adapter esterni | Non interpreta la policy finale |
| Layer 4 — State, Evidence & Observability | Stato, evidenze, oversight, checkpoint, trace e artifact | Non ricombina la direttiva OPA |
| Layer 5 — Evaluation & Continuous Improvement | Evaluation offline implementata; Continuous Improvement differita | Consuma `layer2_trace.v6` esportati senza entrare nella run live |

## 1. Entry point e runtime principale

L'entry point operativo è `src/main_layer2.py`.

- `run_layer2_flow(...)` costruisce o riceve le dipendenze, esegue Layer 1, invoca il grafo usando un `thread_id`, legge lo snapshot finale e persiste gli artifact.
- `resume_layer2_flow(...)` ricompone il grafo con lo stesso checkpointer e riprende l'interrupt con `langgraph.types.Command(resume=...)`.
- `_run_owned_cli_session(...)` è il proprietario CLI del thread: acquisisce/heartbeatta il lease, riceve decisioni dalla inbox e invoca la ripresa. L'opzione `--attach-thread` recupera un thread ancora sospeso.
- `layer2_runtime_governance.composition — compose_layer2_runtime` è la composition root: condivide configurazione del Tool Gateway e registry delle capability tra i nodi e le dipendenze concrete.
- `layer2_runtime_governance.graph — build_runtime_graph` costruisce e compila il `StateGraph`; il checkpointer di default è selezionato da `layer4_state_evidence_observability.infrastructure.checkpointing — build_default_checkpointer` (SQLite locale, PostgreSQL opzionale).

Entrypoint separati:

- `src/main_layer1.py`: solo intake.
- `src/main_layer2_dashboard.py — main`: control plane HTTP/static frontend per HITL; non invoca né riprende LangGraph.
- `services/policy_decision_point/compose.yaml`: avvia OPA 1.15.2 in modalità server su `:8181`, monta il bundle Rego in sola lettura.

## 2. Nodi LangGraph e transizioni

Il grafo è definito esplicitamente in `src/layer2_runtime_governance/graph.py — build_runtime_graph`; gli adapter restano sottili in `runtime/node_adapters.py`.

| Nodo | Adapter / servizio effettivo | Transizione successiva |
|---|---|---|
| `bootstrap_from_intake` | `bootstrap_node` → `lifecycle_service.bootstrap_from_intake` | `planner` |
| `planner` | `build_planner_node` → `planning_service.planner` | `actor` |
| `actor` | `build_actor_node` → `actor_service.actor` | sempre `governance` |
| `governance` | `build_governance_node` → `governance_service.governance` | actor, tool, replanner, review, external execution o finalize secondo stato |
| `tool_gateway` | `build_tool_gateway_node` → `tool_service.tool_gateway` | `observe_tool_result` |
| `observe_tool_result` | `observe_tool_result_node` → `tool_service.observe_tool_result` | governance per una richiesta pendente, replanner, oppure actor |
| `replanner` | `build_replanner_node` → `replanning_service.replanner` | `actor` |
| `human_review` | `build_human_review_node` → `hitl.human_review` | nodo definito da `human_review_resume_state` |
| `external_execution` | `build_external_execution_node` → `external_execution_service.external_execution` | retry esterno, reconciliation review oppure finalize |
| `finalize_bootstrap` | `finalize_node` → `lifecycle_service.finalize_bootstrap` | `END` |

Le funzioni di routing sono in `runtime/routing.py`: `route_after_actor`, `route_after_governance`, `route_after_observe_tool_result`, `route_after_human_review`, `route_after_external_execution`. Un dettaglio importante dell'implementazione corrente è che persino `INTERMEDIATE_OUTPUT` passa per `governance`: `route_after_actor` restituisce incondizionatamente `"governance"`; l'eventuale ritorno ad `actor` è poi deciso dallo stato aggiornato.

## 3. Servizi applicativi e responsabilità

I nodi delegano ai seguenti servizi, anziché incorporare logica nel grafo.

- Intake: `layer1_intake.service — IntakeService`, `TaskSpecBuilder`, `PolicyResolver`, `IntakeResultValidator`; adapter reale OpenAI e JSON policy in `layer1_intake.infrastructure — OpenAITaskSpecParser`, `JsonExecutionPolicyRepository`, `build_intake_service`.
- Planning: `runtime.planning_service — planner`; port e implementazione OpenAI in `layer2_runtime_governance.planning`; replanning in `runtime.replanning_service — replanner` con port `planning.replanning — ReplannerPort`.
- Acting: `runtime.actor_service — actor`, che registra turni, artifacts, scratchpad audit-safe, validazioni e completion candidate. Provider/adattatori sono separati in `execution/` (`factory.build_default_actor`, `providers.bedrock_runtime`, `providers.openai`, parser e action schema).
- Tool loop: `runtime.tool_service — tool_gateway`, `observe_tool_result`. I guard locali gestiscono cache, duplicati, retry/circuito e osservazioni concrete prima/dopo il backend.
- Governance: `runtime.governance_service — governance`, con pipeline OPA-first in `governance_components.opa_pipeline — evaluate_opa_first_governance`.
- Semantic judge: `judge.service` dietro `JudgePort`; il context e i raw facts sono creati da `signal_services.judge_service — build_judge_context`, `build_judge_signal`.
- External execution: `runtime.external_execution_service — external_execution`, con preflight, binding digest, idempotenza, receipt e reconciliation.
- HITL: `hitl — build_human_review_request`, `human_review`; trasporto dashboard/runtime tramite `hitl.decision_inbox — ReviewDecisionInboxPort` e implementazione SQLite `infrastructure.review_decision_inbox — SQLiteReviewDecisionInbox`.

## 4. Contratti Pydantic e stato canonico

### Layer 1: contratti Pydantic

`src/layer1_intake/models.py` usa `pydantic.BaseModel` per i contratti esterni/canonici:

- `TaskSpec`: goal, context, constraints, sensitivity, success criteria ed execution intent;
- `ExecutionPolicy`;
- `TaskCompletenessAssessment`, `StructuredTask`, `IntakeMetadata`, `IntakeResult`;
- `service.IntakeCommand` è il comando validato di ingresso.

Normalizzazione e validazioni sono nei validator di tali modelli; la completezza semantica è la funzione pura `completeness.assess_task_completeness`.

### Layer 2: dataclass e stato

Lo stato canonico del grafo è la dataclass `src/layer2_runtime_governance/domain/task_state.py — TaskState`, trasportata da `runtime/contracts.py — Layer2GraphState` (con `Layer2GraphInput`/`Layer2GraphOutput`). Le sue aree principali sono:

- intake e lifecycle: `intake_payload`, `current_state`, flags, `runtime_thread_id`, `step_history`, `execution_intent`, communication phase;
- piano: `plan_steps`, cursore, metadati planner, `replan_checkpoints`;
- actor: risultato, turni, `executed_steps`, output finale, workspace, scratchpad, step validations, `pending_completion_candidate` e recovery protocol;
- tool/evidence: richiesta corrente e coda, risultati, `observations`, attempt/invocation e contatori;
- external operation: proposal immutabile/digest, revision feedback, request autorizzata, receipt e history;
- governance: history static, dynamic/oversight, behavioral shadow, judge e decisioni OPA;
- HITL: request pendente/ultima e history delle decisioni.

`TaskState.from_intake` stabilisce lo stato iniziale: `NEEDS_CLARIFICATION` diventa `WAITING_HUMAN_REVIEW`, gli intake parziali restano pianificabili con flag, e sensitivity/read-only diventano flags espliciti.

Le strutture di esecuzione e audit sono in `domain/runtime_records.py`: `ExecutedPlanStep`, `ScratchpadEntry`, `StepValidation`, `StepCompletionCandidate`, `ToolAttempt`, `ToolInvocation`, `ReplanCheckpoint`, `AgentWorkspace`. Le semantic plan steps sono `domain/plan.py — PlanStep`, `PlanStepStatus`. I contratti di review sono `domain/review.py — HumanReviewRequest`, `HumanReviewDecision`, `HumanReviewDecisionType`, `ExternalCommunicationState`, `ExternalCommunicationPhase`.

I contratti esterni sono dataclass tipizzate: `layer3_tool_bus_external_capabilities.tool_contract — ToolRequest`, `ToolExecutionResult`, `ToolObservation`; e `external_operations.contracts — ExternalOperationProposal`, payload discriminati, `ExternalExecutionRequest`, `ExternalExecutionReceipt`.

## 5. PEP Python e PDP OPA

Il **Policy Enforcement Point (PEP)** è l'insieme del runtime Layer 2, in particolare `runtime.governance_service — governance` e `_apply_governance_decision`:

1. invoca `evaluate_opa_first_governance`;
2. conserva assessment e `GovernanceDecisionTrace` nello stato;
3. applica esclusivamente la direttiva validata impostando lo stato LangGraph (tool, actor, replan, review, external execution o abort);
4. per una write esterna, `external_execution_service — _preflight_external_execution` ricontrolla invarianti non-politici: intent, capability configurata/abilitata, allowlist MCP, binding proposal/candidate/evidence/digest, autorizzazione e idempotency.

Il **Policy Decision Point (PDP)** è OPA. `governance_components.policy_engine — OPAHttpPolicyEngine` fa POST a:

- `/v1/data/layer2/governance/required_pillars`;
- `/v1/data/layer2/governance/decision`.

Il client trasporto indipendente dal runtime è `src/governance_policy_service/client.py — OPADecisionClient`; invia `{ "input": ... }`, estrae l'envelope OPA `result` e non conosce `TaskState`. La configurazione del servizio è `services/policy_decision_point/compose.yaml`.

L'input del PDP è assemblato in `governance_components.input_document — build_governance_base_facts` e `build_governance_decision_input`, poi verificato contro schema da `validate_governance_decision_input`. Include versione bundle/config, base facts (task, policy, runtime, step, actor, tool, proposal normalizzata, comunicazione, confidence) e tutti i pillar con `raw_facts`, metadata e indisponibilità. L'output atteso è `governance_decision_output.v1`, validato da `decision_parser._validate_governance_decision_output` e proiettato in `GovernanceDecisionTrace`.

## 6. Policy Rego: input, output e regole

Il bundle è `configs/opa/layer2_governance/`:

- `data.json`: `policy_bundle_version=layer2_governance_v4`, `thresholds_version=layer2_governance_thresholds_v2`, massimo tre revisioni e destinazioni/domini proibiti.
- `main.rego`: package `layer2.governance`, endpoint `required_pillars`, staged `judge_admission` e decisione finale `decision`.
- `pillars/static_policy.rego`: hard deny per write in read-only, SQL non-SELECT/ambiguo su profile read-only, recipient/dominio proibito; conserva envelope read-only e segnale clarification.
- `pillars/evidence.rego`: gap strutturale per riferimenti invalidi/duplicati, osservazioni senza contenuto/lineage/step, title-only, burden mancante o non soddisfatto, freshness dichiarata non soddisfatta, provenienza circolare, letterali non ancorati o digest candidate–snapshot–selection–operation non coerenti.
- `pillars/trajectory.rego`: valuta liveness, ammissibilità/stallo e tetto del replan, resubmission invariata o già rifiutata, fallimenti consecutivi, churn e soglia revisioni.
- `pillars/judge.rego`: proietta assessment semantico e binding fra digest payload/evidence.
- `pillars/risk.rego` e `pillars/behavioral.rego`: proiettano esplicitamente `mode: shadow` e `decision_influence: false`.

`main.rego — required_pillars` è event-sensitive: `intermediate_output` e una proposta non fattuale richiedono Trajectory per la liveness; una proposta fattuale richiede Evidence+Trajectory; `needs_replan` richiede Trajectory; completion locale e candidato external richiedono Evidence+Trajectory. Solo dopo questo gate Rego ammette il Judge per un candidato governed external digestato. L'output finale contiene `directive`, `policy_decision`, `reason_codes`, `judge_admission`, risultati dei sei pillar, invarianti verificati, `requires_human_review` e versioni.

## 7. Evidence, Operational Risk e Trajectory pillars

**Evidence Integrity & Provenance (attivo).** Python calcola fatti deterministici in `signal_services.evidence_service — build_evidence_signal`; digest e manifest provengono da `domain.evidence — build_evidence_snapshot_manifest`, `build_evidence_selection_digest`. Rego non valuta entailment semantico: richiede integrità di ref, contenuto, lineage, associazione allo step, burden obbligatorio per step fattuali, freshness da data attribuita alla fonte, non-circolarità strutturale, ancoraggio letterale dei token estraibili e binding immutabile della completion candidate. I deficit sono esposti nei fatti `deficit_codes`; a fronte di un gap, `main.rego` emette `request_more_evidence`, con escalation a review al tetto configurato; un burden mancante in un checkpoint legacy richiede direttamente revisione umana.

**Trajectory Health (attivo).** `signal_services.trajectory_service — build_trajectory_signal` esporta fatti osservabili: turni, tool/observation/completed steps, checkpoints di replan, protocol failure, reason, novità osservabile e revisioni. `pillars/trajectory.rego` decide se il replan è ammissibile; lo stallo/una resubmission invariata si trasformano in review, non in progresso fittizio. `runtime.replanning_service — replanner` sostituisce solo step corrente e futuri e conserva gli step completati immutabili.

**Operational Risk / consequence (shadow-only).** `signal_services.risk_service — build_risk_signal` costruisce fatti solo per una `ExternalOperationProposal`; `pillars/risk.rego` li espone con `decision_influence: false`. È quindi osservabile, persistibile e valutabile, ma non può produrre directive né rendere fail-closed la pipeline.

**Behavioral (shadow-only).** `signal_services.behavioral_service — build_behavioral_signal` e `layer4_state_evidence_observability.behavioral.service` interrogano/persistono un read model SQLite per similarity, deviation e novelty. `opa_pipeline.evaluate_opa_first_governance` lo campiona a boundary stabili; `pillars/behavioral.rego` ne esclude esplicitamente l'influenza sulla decisione.

Il judge è un pillar attivo ma distinto: il context digest-bound è costruito in `signal_services.judge_service`, il bundle evidence in `judge.evidence_bundle — build_judge_evidence_bundle`, e Rego ne interpreta `safe`, `needs_revision`, `unsafe`, `uncertain` solo dopo ammissione OPA.

## 8. Decision Combiner

Non esiste un combiner Python alternativo: il **Decision Combiner è `configs/opa/layer2_governance/main.rego — decision`**. La precedenza `else` effettiva è:

1. mismatch di versioni;
2. hard deny statico;
3. judge richiesto ma assente/non bound;
4. pillar attivo indisponibile;
5. clarification;
6. replan stall, candidate ripetuto/rifiutato o invariato;
7. replan ammissibile;
8. judge unsafe/uncertain; limite revisioni; `needs_revision`;
9. evidence gap;
10. bassa confidence di candidato external o approval mode;
11. envelope read-only;
12. continue.

Le direttive sono `stop`, `wait_human_review`, `ask_user_clarification`, `replan`, `revise_action`, `request_more_evidence`, `continue_read_only`, `continue`. Python le enumera e valida in `governance_components.contracts — GovernanceDirective` e le applica in `governance_service._apply_governance_decision`, senza ri-calcolarne la precedenza.

## 9. HITL Gateway

Il gateway HITL ha due livelli.

- **Nel grafo**: `hitl.human_review` genera una request tipizzata con `build_human_review_request`, chiama `langgraph.types.interrupt`, e alla ripresa normalizza/verifica `HumanReviewDecision` (request-bound, digest-bound se applicabile) prima di impostare lo stato di resume.
- **Control plane durevole**: `ReviewDecisionInboxPort` definisce acquire/heartbeat/release owner, `submit`, `claim_next`, `mark_consumed`, `mark_failed`; `SQLiteReviewDecisionInbox` implementa la coda SQLite. `main_layer2.py` mantiene la proprietà del runtime e consuma la coda; `main_layer2_dashboard.py` legge artifact/snapshot e invia una decisione, ma non esegue LangGraph.

Le azioni sono reason-scoped via `HumanReviewDecisionType`: approve, approve read-only, modify, more evidence, replan, clarification, abort. `MODIFY_AND_CONTINUE` ricostruisce il payload, produce un digest nuovo e invalida autorizzazione/judge precedenti. L'autorizzazione di external execution è consentita solo per la request/digest appropriata; `external_execution_service._has_exact_authorization` controlla la source OPA o human review.

## 10. Trace, observability ed evaluation

Il trace è una proiezione read-only di `TaskState`.

- `observability.builder — build_layer2_runtime_trace` crea `Layer2RuntimeTrace` dai modelli in `observability.models.py`: runtime/intake, piano, actor, tool request/result/observation, pass OPA, assessment, judge, review, communication, execution receipt.
- `observability.views — layer2_trace_to_json`, `build_compact_trace_from_runtime_trace` serializzano vista completa e compatta; `trace_summary — build_trace_summary_markdown` produce la vista leggibile.
- `infrastructure.runtime_artifacts — RuntimeArtifactStore`, `persist_runtime_artifacts` scrivono per thread il trace JSON, `trace_summary.md`, dashboard snapshot e runtime summary, inclusa la classificazione `termination_reason` quando il runtime termina per denial, abort umano, failure/invariant dell'esecuzione esterna o failure runtime. Il contratto esterno chiuso è `schemas/layer2_trace.schema.json` (`layer2_trace.v6`).
- Checkpoint e artifact non sono sinonimi: `infrastructure.checkpointing — build_default_checkpointer` conserva lo stato/interruption LangGraph; gli artifact sono read models e non possono ricostruire un interrupt.

Layer 5 Evaluation è implementato come percorso offline: importa solo `layer2_trace.v6` nel proprio SQLite, conserva annotazioni separate e produce metriche/report. Il feedback loop di Continuous Improvement resta differito e non entra nel runtime live.

## 11. Tool esterni e boundary

### Tool di acquisizione evidence (attivi)

Il solo ingresso nel backend è `tools.gateway — OpenAIToolGateway.execute`, preceduto da `tools.security.validate_tool_request` e dai guard del runtime `tool_service._guarded_tool_result`.

- `web_search`: backend OpenAI, configurato e abilitato in `configs/layer2_tool_gateway.json`; adapter `tools.adapters.openai_search — OpenAIWebSearchAdapter`.
- `fetch_url_text`: backend HTTP, solo HTTPS, configurato e abilitato; adapter `tools.adapters.http_fetch — HttpFetchAdapter` ed extractor HTML/PDF.

`ToolRequest` è una dataclass con enum `ToolName`; l'actor non emette stringhe di tool call opache. Il gateway normalizza il backend in `ToolExecutionResult` con tuple di `ToolObservation`; `observe_tool_result` conserva solo contenuto/osservazione concreta e locator, senza tassonomia semantica della fonte. Le enum MCP locali esistono nel contratto per test, ma non sono abilitate nella configurazione attiva mostrata.

### Capability con side effect (implementate, disabilitate di default)

`configs/layer2_external_capabilities.json` contiene email SMTP (`communication.email.send`) e statement database (`database.statement.execute`), ma entrambe le capability e i rispettivi server MCP stdio hanno `enabled: false`.

Il confine per una futura attivazione è più ristretto del Tool Gateway read-only:

- `external_operations.runtime_projection — prepare_external_operation_proposal` normalizza la candidate in `ExternalOperationProposal` immutabile;
- `external_operations.contracts — compute_payload_digest`, `ExternalExecutionRequest`, `ExternalExecutionReceipt` vincolano payload/intent/idempotency;
- `external_operations.registry — ExternalCapabilityRegistry`, `_validate_registry` richiedono connector MCP, server enabled, tool allowlisted, boundary `governed_external` e supporto idempotenza;
- `runtime.external_execution_service — _preflight_external_execution` applica tutte le verifiche prima della chiamata;
- `external_capability_mcp` espone gli adapter SMTP/database; `external_operations.mcp.executor` effettua il binding capability → MCP tool.

Quindi nessun testo dell'actor può decidere comando, server, tool o credenziali; può al più produrre una proposta, poi digest, OPA/judge/HITL e registry limitano il passaggio a un executor.

## 12. Percorsi di finalize, replanning, clarification, review e abort

**Finalize.** `lifecycle_service.finalize_bootstrap` è l'unico ultimo nodo e marca `bootstrap_completed`; prima verifica `runtime.completion_contract — terminal_execution_contract_satisfied`. Per `EXECUTE_AFTER_GOVERNANCE`, `COMPLETED` richiede piano esaurito e receipt eseguita digest-bound; per proposal-only una proposal OPA-accettata può chiudere come artifact locale (`governance_service._route_authorized_external_operation`).

**Replanning.** L'actor emette `ActorResultType.NEEDS_REPLAN` con blocking reason (`actor_contract.py`). OPA valuta trajectory: se ammesso, `governance_service._apply_governance_decision` imposta `REPLANNING`; `replanning_service.replanner` conserva gli step completati, sostituisce solo corrente+futuri, crea `ReplanCheckpoint` e ritorna ad actor. Richieste ripetute senza base osservabile vanno a HITL.

**Clarification.** Intake `NEEDS_CLARIFICATION` parte direttamente `WAITING_HUMAN_REVIEW` tramite `TaskState.from_intake`. Durante l'esecuzione l'actor può produrre `ASK_CLARIFICATION`; static policy la segnala e Rego emette `ask_user_clarification`, quindi governance costruisce la request e il nodo `human_review` interrompe. La decisione tipizzata riprende allo stato indicato nella request.

**Review e revisione.** `WAIT_HUMAN_REVIEW` copre approval capability, low confidence external, judge unsafe/uncertain, pillar/version unavailable, replan stall e candidate ripetuti; `ASK_USER_CLARIFICATION` usa lo stesso meccanismo ma con azioni consentite diverse. `REVISE_ACTION` non autorizza side effect: `governance_service._apply_action_revision` conserva feedback judge, invalida binding precedenti e rimanda all'actor. Receipt `INDETERMINATE` entra nella review di reconciliation in `external_execution_service._route_indeterminate_to_human_reconciliation`; non viene ritentata automaticamente.

**Abort.** Direttiva OPA `STOP` porta a `RuntimeState.ABORTED`, cancella tool/review pendenti e marca communication `BLOCKED` (`governance_service._apply_governance_decision`). External execution fallita deterministicamente, preflight non superato o drift di digest porta a receipt/reject e infine `ABORTED`; solo una failure retryable entro `max_attempts` ritorna a `READY_EXTERNAL_EXECUTION`.

## Conclusione architetturale

L'implementazione realizza un PEP LangGraph stateful, checkpointed e auditabile che sorveglia un planner/actor operativo. OPA è un PDP esterno e stateless con input/output versionati e precedenza Rego esplicita. Evidence e trajectory hanno influenza attiva; risk e behavioral sono deliberatamente shadow-only. Il passaggio da proposta a side effect è protetto da proposal normalizzata, digest/evidence binding, giudizio semantico opzionale ma policy-required, autorizzazione umana reason-scoped quando necessaria, registry capability-bound, idempotenza e receipt/reconciliation.
