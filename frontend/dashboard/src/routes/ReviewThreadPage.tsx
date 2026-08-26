import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { fetchReviewDetail, resumeReviewThread } from "../api";
import { DecisionForm } from "../components/DecisionForm";
import { executionIntentLabel, executionStatusLabel, externalExecutionNarrative } from "../components/executionPresentation";
import { KeyValueGrid } from "../components/KeyValueGrid";
import { ReviewContextPanel } from "../components/ReviewContextPanel";
import { runtimeStateBadgeClass, runtimeStateLabel } from "../components/runtimeStatePresentation";
import { usePolledResource } from "../hooks";
import type { ReviewDetailResponse } from "../types";

function valueToString(value: unknown) {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

export function ReviewThreadPage() {
  const { threadId = "" } = useParams();
  const navigate = useNavigate();
  const decodedThreadId = decodeURIComponent(threadId);
  const resource = usePolledResource(() => fetchReviewDetail(decodedThreadId), [decodedThreadId]);
  const [detailOverride, setDetailOverride] = useState<ReviewDetailResponse | null>(null);

  useEffect(() => {
    setDetailOverride(resource.data);
  }, [resource.data]);

  const detail = detailOverride ?? resource.data;
  const snapshot = detail?.dashboard_snapshot;
  const pendingRequest = snapshot?.human_review?.pending_request;
  const draftBody = pendingRequest?.external_communication_draft?.body ?? null;
  const taskGoal = valueToString(detail?.runtime_summary?.task_goal);
  const governance = snapshot?.governance as Record<string, unknown> | null | undefined;
  const oversight = snapshot?.oversight as Record<string, unknown> | null | undefined;
  const externalExecution = snapshot?.external_execution;

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

      {resource.error ? <div className="alert alert-error">{resource.error}</div> : null}
      {resource.loading && !detail ? <div className="skeleton" style={{ height: 120 }} /> : null}

      {detail && snapshot ? (
        <>
          {pendingRequest ? (
            <ReviewContextPanel taskGoal={taskGoal} snapshot={snapshot} />
          ) : null}

          {draftBody ? (
            <section className="card card-info">
              <div className="card-header">
                <div>
                  <h2 className="card-title">Testo prodotto</h2>
                  <p className="card-caption">Questo è il testo che verrà inviato se approvi.</p>
                </div>
                <span className="badge badge-info">
                  {pendingRequest?.external_communication_draft?.channel ?? "bozza"}
                </span>
              </div>
              <div className="card-body">
                <pre className="produced-text">{draftBody}</pre>
              </div>
            </section>
          ) : null}

          {pendingRequest ? (
            <DecisionForm
              detail={detail}
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
              <div className="card-body muted-panel">Nessuna azione di revisione in sospeso per questo thread.</div>
            </section>
          )}

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
