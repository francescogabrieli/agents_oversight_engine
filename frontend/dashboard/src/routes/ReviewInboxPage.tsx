import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { fetchReviewInbox } from "../api";
import { communicationPhaseLabel, executionIntentLabel, executionStatusLabel } from "../components/executionPresentation";
import { humanReviewDecisionLabel } from "../components/humanReviewPresentation";
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
        {item.review_summary ? (
          <p className="inbox-row-summary">{item.review_summary}</p>
        ) : null}
        {item.allowed_decisions.length > 0 ? (
          <div className="inbox-row-actions">
            {item.allowed_decisions.map((d) => (
              <span key={d} className="action-chip">{humanReviewDecisionLabel(d)}</span>
            ))}
          </div>
        ) : null}
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
  const { data, error, loading } = usePolledResource(fetchReviewInbox, []);
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
          <div className="stat-value">{loading ? "—" : items.length}</div>
        </div>
      </div>

      {error ? <div className="alert alert-error">{error}</div> : null}

      <div className="card">
        <div className="card-header">
          <h2 className="card-title">Coda attiva</h2>
          <span className="badge badge-waiting">{items.length} in attesa</span>
        </div>
        <div className="card-body">
          {loading && items.length === 0 ? (
            <div className="skeleton" style={{ height: 72 }} />
          ) : null}

          {!loading && items.length === 0 ? (
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
              <h2 className="card-title">Completate</h2>
              <p className="card-caption">Decisioni inviate negli ultimi due minuti.</p>
            </div>
            <span className="badge badge-completed">{completedItems.length} risolte</span>
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
          >
            <div className="decision-success-icon" aria-hidden="true">✓</div>
            <h2 id="decision-success-title">Decisione inviata correttamente</h2>
            <p>{decisionFeedback}</p>
            <button className="btn btn-primary" type="button" onClick={closeDecisionModal}>
              Chiudi
            </button>
          </section>
        </div>
      ) : null}
    </div>
  );
}
