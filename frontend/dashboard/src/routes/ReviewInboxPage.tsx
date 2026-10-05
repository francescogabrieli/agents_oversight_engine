import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { fetchReviewInbox } from "../api";
import { communicationPhaseLabel, executionIntentLabel, executionStatusLabel } from "../components/executionPresentation";
import { humanReviewDecisionLabel } from "../components/humanReviewPresentation";
import { DecisionDeliveryStatus } from "../components/DecisionDeliveryStatus";
import { reasonCodeExplanation } from "../components/reasonCodePresentation";
import {
  RuntimeStateIcon,
  runtimeStateBadgeClass,
  runtimeStateLabel,
  runtimeStateRowClass,
} from "../components/runtimeStatePresentation";
import { usePolledResource } from "../hooks";
import type { ReviewInboxEntry } from "../types";

function formatTimestamp(value: string | null) {
  if (!value) return "—";
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(ts);
}

function InboxRow({ item, interactive = true }: { item: ReviewInboxEntry; interactive?: boolean }) {
  const content = (
    <>
      <div className={runtimeStateRowClass(item.runtime_state)}>
        <RuntimeStateIcon state={item.runtime_state} />
      </div>
      <div className="inbox-row-body">
        <div className="inbox-row-top">
          <div className="inbox-row-left">
            <span className={runtimeStateBadgeClass(item.runtime_state)}>
              {runtimeStateLabel(item.runtime_state) ?? "sconosciuto"}
            </span>
            {item.external_communication_phase ? (
              <span className="badge badge-neutral">
                {communicationPhaseLabel(item.external_communication_phase)}
              </span>
            ) : null}
            {item.execution_intent ? (
              <span className="badge badge-neutral">{executionIntentLabel(item.execution_intent)}</span>
            ) : null}
            {item.external_execution_status ? (
              <span className="badge badge-info">{executionStatusLabel(item.external_execution_status)}</span>
            ) : null}
            {item.decision_type ? (
              <span className="badge badge-completed">{humanReviewDecisionLabel(item.decision_type)}</span>
            ) : null}
            <p className="inbox-row-title">{item.task_goal ?? "Attività senza titolo"}</p>
          </div>
          <div className="inbox-row-meta">
            <span className="inbox-row-timestamp">{formatTimestamp(item.updated_at)}</span>
            <span className="inbox-row-id">{item.thread_id}</span>
          </div>
        </div>
        {(() => {
          // Prefer a plain-language Italian explanation of the primary reason code;
          // OPA's raw English audit sentence is a fallback for an unmapped code only.
          const primaryReasonCode = item.reason_codes?.[0] ?? null;
          const clearSummary = primaryReasonCode ? reasonCodeExplanation(primaryReasonCode) : null;
          const summary = clearSummary ?? item.review_summary;
          return summary ? <p className="inbox-row-summary">{summary}</p> : null;
        })()}
        {item.allowed_decisions.length > 0 ? (
          <div className="inbox-row-actions">
            <span>Apri la revisione per scegliere: {item.allowed_decisions.map(humanReviewDecisionLabel).join(" · ")}</span>
          </div>
        ) : null}
        {!interactive ? <DecisionDeliveryStatus status={item.delivery_status} ownerAvailable={item.owner_available} /> : null}
      </div>
    </>
  );
  if (!interactive) {
    return <div className="inbox-row inbox-row-resolved" aria-disabled="true">{content}</div>;
  }
  return (
    <Link to={`/threads/${encodeURIComponent(item.thread_id)}`} className="inbox-row">
      {content}
    </Link>
  );
}

export function ReviewInboxPage() {
  const { data, error, loading, updatedAt } = usePolledResource(fetchReviewInbox, []);
  const items = data?.items ?? [];
  const completedItems = data?.completed_items ?? [];
  const location = useLocation();
  const navigate = useNavigate();
  const locationFeedback = (
    location.state as { decisionFeedback?: string } | null
  )?.decisionFeedback;
  const [decisionFeedback, setDecisionFeedback] = useState<string | null>(
    locationFeedback ?? null,
  );
  const closeButton = useRef<HTMLButtonElement>(null);

  // The acknowledgement has one control: focus it and restore focus on dismissal.
  useEffect(() => {
    if (!decisionFeedback) return;
    const previous = document.activeElement;
    closeButton.current?.focus();
    return () => { if (previous instanceof HTMLElement) previous.focus(); };
  }, [decisionFeedback]);

  const closeDecisionModal = () => {
    setDecisionFeedback(null);
    navigate("/", { replace: true, state: null });
  };

  return (
    <div style={{ display: "grid", gap: 20 }}>
      <div className="page-header">
        <p className="page-header-eyebrow">Supervisione umana</p>
        <h1 className="page-header-title">Attività da revisionare</h1>
        <p className="page-header-sub">
          Attività in attesa di una tua decisione. Aggiornamento automatico ogni 2&nbsp;secondi.
        </p>
      </div>

      <div className="stat-row">
        <div className="stat-item">
          <div className="stat-label">In attesa</div>
          <div className="stat-value">{!data ? "—" : items.length}</div>
        </div>
      </div>

      {error ? <div className="alert alert-error" role="alert">{error}</div> : null}
      <p className="connection-status" role="status">{error ? "Connessione interrotta: la coda potrebbe non essere aggiornata." : updatedAt ? `Ultimo aggiornamento della coda: ${new Date(updatedAt).toLocaleTimeString()}.` : "Caricamento della coda…"}</p>

      <div className="card">
        <div className="card-header">
          <h2 className="card-title">Coda attiva</h2>
          <span className="badge badge-waiting">{data ? `${items.length} in attesa` : "Dati non disponibili"}</span>
        </div>
        <div className="card-body">
          {loading && items.length === 0 ? (
            <div className="skeleton" style={{ height: 72 }} />
          ) : null}

          {!loading && !error && data && items.length === 0 ? (
            <div className="empty-state">
              <span className="empty-state-icon">✓</span>
              Nessuna attività in attesa di revisione.
            </div>
          ) : null}

          <div className="inbox-list">
            {items.map((item) => (
              <InboxRow key={item.thread_id} item={item} />
            ))}
          </div>
        </div>
      </div>

      {completedItems.length > 0 ? (
        <div className="card completed-reviews-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">Decisioni inviate</h2>
              <p className="card-caption">Invii degli ultimi due minuti, non run completate. Verifica sotto lo stato di consegna.</p>
            </div>
            <span className="badge badge-info">{completedItems.length} inviate</span>
          </div>
          <div className="card-body">
            <div className="inbox-list">
              {completedItems.map((item) => (
                <InboxRow key={`${item.request_id}-${item.updated_at}`} item={item} interactive={false} />
              ))}
            </div>
          </div>
        </div>
      ) : null}

      {decisionFeedback ? (
        <div className="modal-backdrop" role="presentation">
          <section
            className="decision-success-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="decision-success-title"
            onKeyDown={(event) => {
              if (event.key === "Escape") closeDecisionModal();
              if (event.key === "Tab") {
                event.preventDefault();
                closeButton.current?.focus();
              }
            }}
          >
            <div className="decision-success-icon" aria-hidden="true">✓</div>
            <h2 id="decision-success-title">Decisione inviata correttamente</h2>
            <p>{decisionFeedback}</p>
            <button ref={closeButton} className="btn btn-primary" type="button" onClick={closeDecisionModal}>
              Chiudi
            </button>
          </section>
        </div>
      ) : null}
    </div>
  );
}
