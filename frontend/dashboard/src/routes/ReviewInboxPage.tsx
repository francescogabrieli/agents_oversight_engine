import { Link } from "react-router-dom";
import { fetchReviewInbox } from "../api";
import { usePolledResource } from "../hooks";
import type { ReviewInboxEntry } from "../types";

function statusClass(runtimeState: string | null) {
  if (runtimeState === "waiting_human_review") return "inbox-row-status inbox-row-status-waiting";
  if (runtimeState === "completed") return "inbox-row-status inbox-row-status-completed";
  if (runtimeState === "aborted") return "inbox-row-status inbox-row-status-aborted";
  return "inbox-row-status inbox-row-status-default";
}

function statusIcon(runtimeState: string | null) {
  if (runtimeState === "waiting_human_review") return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
    </svg>
  );
  if (runtimeState === "completed") return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12"/>
    </svg>
  );
  if (runtimeState === "aborted") return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
    </svg>
  );
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/>
    </svg>
  );
}

function badgeClass(runtimeState: string | null) {
  if (runtimeState === "waiting_human_review") return "badge badge-waiting";
  if (runtimeState === "completed") return "badge badge-completed";
  if (runtimeState === "aborted") return "badge badge-aborted";
  return "badge badge-default";
}

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

function InboxRow({ item }: { item: ReviewInboxEntry }) {
  return (
    <Link
      to={`/threads/${encodeURIComponent(item.thread_id)}`}
      className="inbox-row"
    >
      <div className={statusClass(item.runtime_state)}>
        {statusIcon(item.runtime_state)}
      </div>
      <div className="inbox-row-body">
        <div className="inbox-row-top">
          <div className="inbox-row-left">
            <span className={badgeClass(item.runtime_state)}>
              {item.runtime_state ?? "unknown"}
            </span>
            {item.external_communication_phase ? (
              <span className="badge badge-neutral">{item.external_communication_phase}</span>
            ) : null}
            <p className="inbox-row-title">{item.task_goal ?? "Untitled task"}</p>
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
              <span key={d} className="action-chip">{d}</span>
            ))}
          </div>
        ) : null}
      </div>
    </Link>
  );
}

export function ReviewInboxPage() {
  const { data, error, loading } = usePolledResource(fetchReviewInbox, []);
  const items = data ?? [];

  return (
    <div style={{ display: "grid", gap: 20 }}>
      <div className="page-header">
        <p className="page-header-eyebrow">Human-in-the-loop</p>
        <h1 className="page-header-title">Review inbox</h1>
        <p className="page-header-sub">
          Active threads waiting for operator review. Polling every 2&nbsp;seconds.
        </p>
      </div>

      <div className="stat-row">
        <div className="stat-item">
          <div className="stat-label">Active pending</div>
          <div className="stat-value">{loading ? "—" : items.length}</div>
        </div>
        <div className="stat-item">
          <div className="stat-label">Inbox cutoff</div>
          <div className="stat-value">2 min</div>
        </div>
        <div className="stat-item">
          <div className="stat-label">Polling</div>
          <div className="stat-value">2 sec</div>
        </div>
      </div>

      {error ? <div className="alert alert-error">{error}</div> : null}

      <div className="card">
        <div className="card-header">
          <h2 className="card-title">Active queue</h2>
          <span className="badge badge-waiting">{items.length} pending</span>
        </div>
        <div className="card-body">
          {loading && items.length === 0 ? (
            <div className="skeleton" style={{ height: 72 }} />
          ) : null}

          {!loading && items.length === 0 ? (
            <div className="empty-state">
              <span className="empty-state-icon">✓</span>
              No pending reviews in the active queue.
            </div>
          ) : null}

          <div className="inbox-list">
            {items.map((item) => (
              <InboxRow key={item.thread_id} item={item} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
