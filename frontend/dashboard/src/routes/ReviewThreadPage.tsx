import { Link, useNavigate, useParams } from "react-router-dom";
import { fetchReviewDetail, resumeReviewThread } from "../api";
import { DecisionForm } from "../components/DecisionForm";
import { ProposalPanel } from "../components/ProposalPanel";
import { PriorDecisionsPanel } from "../components/PriorDecisionsPanel";
import { DecisionDeliveryStatus } from "../components/DecisionDeliveryStatus";
import { executionIntentLabel, executionStatusLabel, externalExecutionNarrative } from "../components/executionPresentation";
import { KeyValueGrid } from "../components/KeyValueGrid";
import { ReviewContextPanel } from "../components/ReviewContextPanel";
import { runtimeStateBadgeClass, runtimeStateLabel } from "../components/runtimeStatePresentation";
import { usePolledResource } from "../hooks";

function valueToString(value: unknown) {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

export function ReviewThreadPage() {
  const { threadId = "" } = useParams();
  const navigate = useNavigate();
  // React Router has already decoded this parameter.
  const decodedThreadId = threadId;
  const resource = usePolledResource(() => fetchReviewDetail(decodedThreadId), [decodedThreadId]);
  const detail = resource.data;
  const snapshot = detail?.dashboard_snapshot;
  const pendingRequest = snapshot?.human_review?.pending_request;
  const taskGoal = valueToString(detail?.runtime_summary?.task_goal);
  const governance = snapshot?.governance as Record<string, unknown> | null | undefined;
  const oversight = snapshot?.oversight as Record<string, unknown> | null | undefined;
  const externalExecution = snapshot?.external_execution;
  const isReconciliation = pendingRequest?.reason_codes?.includes("external_execution_reconciliation_required");

  return (
    <div className="review-workspace">
      <section className="review-header">
        <Link to="/" className="back-link">
          <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M9 2L4 7l5 5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Torna alla coda
        </Link>
        <div className="review-header-main">
          <div>
            <p className="page-header-eyebrow">Revisione umana</p>
            <h1 className="review-title">{taskGoal ?? "Attività in attesa di revisione"}</h1>
          </div>
          <div className="review-header-status">
            {snapshot ? (
              <span className={runtimeStateBadgeClass(snapshot.runtime_state)}>
                {runtimeStateLabel(snapshot.runtime_state)}
              </span>
            ) : null}
            {snapshot?.resume_state ? (
              <span className="badge badge-neutral">ripresa: {runtimeStateLabel(snapshot.resume_state)}</span>
            ) : null}
          </div>
        </div>
        <div className="review-thread-id">{decodedThreadId}</div>
      </section>

      {resource.error ? <div className="alert alert-error" role="alert">{resource.error}</div> : null}
      <p className="connection-status" role="status">
        {resource.error ? "Connessione interrotta: sono mostrati gli ultimi dati ricevuti." : resource.updatedAt ? `Dashboard aggiornata alle ${new Date(resource.updatedAt).toLocaleTimeString()}. Questo non attesta che il runtime sia attivo.` : "Connessione alla dashboard in corso…"}
      </p>
      {resource.loading && !detail ? <div className="skeleton" style={{ height: 120 }} /> : null}
      {detail && !snapshot ? <div className="alert alert-error" role="alert">Dettagli della revisione non disponibili. Lo snapshot non è presente; nessuna decisione può essere inviata.</div> : null}
      {detail?.decision_submission ? <DecisionDeliveryStatus status={detail.decision_submission.delivery_status} ownerAvailable={detail.decision_submission.owner_available} /> : null}

      {detail && snapshot ? (
        <>
          {pendingRequest ? <nav className="review-action-nav" aria-label="Navigazione della revisione">
            <span>Richiesta in attesa della tua decisione</span>
            <a className="btn btn-primary" href="#review-decision">Vai alla decisione ↓</a>
          </nav> : null}
          {pendingRequest ? (
            <ReviewContextPanel key={pendingRequest.request_id} taskGoal={taskGoal} snapshot={snapshot} />
          ) : null}

          {pendingRequest ? <ProposalPanel request={pendingRequest} /> : null}

          {externalExecution ? (
            <section className="card">
              <div className="card-header">
                <h2 className="card-title">Operazione esterna</h2>
                <span className={runtimeStateBadgeClass(externalExecution.status ?? snapshot.runtime_state)}>
                  {executionStatusLabel(externalExecution.status) ??
                    executionIntentLabel(externalExecution.execution_intent)}
                </span>
              </div>
              <div className="card-body-sm">
                <p className="review-context-summary" style={{ margin: 0 }}>
                  {externalExecutionNarrative(
                    externalExecution.execution_intent,
                    externalExecution.status,
                  )}
                  {externalExecution.summary ? ` ${externalExecution.summary}` : ""}
                </p>
                {externalExecution.error_code ? (
                  <div className="alert alert-error" style={{ marginTop: 10 }}>
                    {externalExecution.error_code}
                  </div>
                ) : null}
                {isReconciliation ? <KeyValueGrid title="Esito da riconciliare" values={[
                  { label: "Riferimento esterno", value: externalExecution.external_reference ?? "Non disponibile" },
                  { label: "Connettore", value: externalExecution.connector_id ?? "Non disponibile" },
                  { label: "Tentativi registrati", value: externalExecution.attempt_count },
                ]} /> : null}
                <details className="technical-details">
                  <summary className="technical-details-summary">Mostra i dettagli tecnici</summary>
                  <div className="technical-details-body">
                    <KeyValueGrid
                      title="Ricevuta dell'esecuzione"
                      values={[
                        { label: "Intento", value: executionIntentLabel(externalExecution.execution_intent) },
                        { label: "Capacità", value: externalExecution.capability_id ?? null },
                        { label: "Stato", value: executionStatusLabel(externalExecution.status) },
                        { label: "Digest", value: externalExecution.payload_digest ?? null },
                        { label: "Connettore", value: externalExecution.connector_id ?? null },
                        {
                          label: "Riferimento esterno",
                          value: externalExecution.external_reference ?? null,
                        },
                        { label: "Errore", value: externalExecution.error_code ?? null },
                      ]}
                    />
                  </div>
                </details>
              </div>
            </section>
          ) : null}

          {pendingRequest ? (
            <DecisionForm
              detail={detail}
              disabled={Boolean(resource.error)}
              onSubmit={async (payload) => {
                const nextDetail = await resumeReviewThread(decodedThreadId, payload);
                navigate("/", {
                  replace: true,
                  state: {
                    decisionFeedback:
                      nextDetail.operator_feedback ?? "Decisione inviata correttamente.",
                  },
                });
              }}
            />
          ) : (
            <section className="card">
              <div className="card-body muted-panel">
                <p role="status">Nessuna azione di revisione in sospeso per questo thread.</p>
                <p>{snapshot.is_terminal
                  ? `Stato finale registrato: ${runtimeStateLabel(snapshot.runtime_state)}.`
                  : "Per seguire la prosecuzione dell'attività, consulta il terminale del runtime."}</p>
                <PriorDecisionsPanel decisions={snapshot.human_review?.decision_history ?? []} currentOperationDigest={null} />
              </div>
            </section>
          )}


          <details className="technical-details">
            <summary className="technical-details-summary">Mostra tutti i dettagli tecnici</summary>
            <div className="technical-details-body">
              <KeyValueGrid
                title="Richiesta di revisione"
                values={[
                  { label: "ID richiesta", value: pendingRequest?.request_id ?? null },
                  { label: "Passo in revisione", value: snapshot.current_plan_step?.step_id ?? null },
                  { label: "Obiettivo del passo", value: snapshot.current_plan_step?.objective ?? null },
                  { label: "Riepilogo revisione", value: pendingRequest?.review_summary ?? null },
                  {
                    label: "Decisioni consentite",
                    value: pendingRequest?.allowed_decisions?.join(", ") ?? null,
                  },
                ]}
              />
              <KeyValueGrid
                title="Riepilogo runtime"
                values={[
                  { label: "Thread", value: detail.thread_id },
                  { label: "Stato", value: runtimeStateLabel(snapshot.runtime_state) },
                  { label: "Stato di ripresa", value: runtimeStateLabel(snapshot.resume_state) },
                  { label: "Revisione in sospeso", value: snapshot.has_pending_review ? "Sì" : "No" },
                  { label: "Terminale", value: snapshot.is_terminal ? "Sì" : "No" },
                  { label: "Codici motivo", value: snapshot.reason_codes.join(", ") || null },
                ]}
              />
              {snapshot.current_plan_step ? (
                <KeyValueGrid
                  title="Passo del piano attuale"
                  values={[
                    { label: "ID passo", value: snapshot.current_plan_step.step_id },
                    { label: "Stato", value: snapshot.current_plan_step.status },
                    { label: "Obiettivo", value: snapshot.current_plan_step.objective },
                    { label: "Esito atteso", value: snapshot.current_plan_step.expected_outcome },
                    { label: "Motivazione", value: snapshot.current_plan_step.rationale },
                  ]}
                />
              ) : null}
              <KeyValueGrid
                title="Governance"
                values={Object.entries((governance ?? {}) as Record<string, unknown>).map(
                  ([label, value]) => ({ label, value: valueToString(value) }),
                )}
              />
              <KeyValueGrid
                title="Supervisione"
                values={Object.entries((oversight ?? {}) as Record<string, unknown>).map(
                  ([label, value]) => ({ label, value: valueToString(value) }),
                )}
              />
              <KeyValueGrid
                title="Comunicazione esterna"
                values={Object.entries(
                  (snapshot.external_communication ?? {}) as Record<string, unknown>,
                ).map(([label, value]) => ({ label, value: valueToString(value) }))}
              />
              <KeyValueGrid
                title="Esecuzione esterna"
                values={Object.entries(
                  (snapshot.external_execution ?? {}) as Record<string, unknown>,
                ).map(([label, value]) => ({ label, value: valueToString(value) }))}
              />
              <section className="card">
                <div className="card-header">
                  <h2 className="card-title">Snapshot grezzo</h2>
                </div>
                <div className="card-body-sm">
                  <pre className="raw-json-block">{JSON.stringify(detail, null, 2)}</pre>
                </div>
              </section>
            </div>
          </details>
        </>
      ) : null}
    </div>
  );
}
