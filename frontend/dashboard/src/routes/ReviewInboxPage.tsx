import { useState } from "react";
import { Link } from "react-router-dom";
import { fetchReviewInbox } from "../api";
import { usePolledResource } from "../hooks";
import type { ReviewInboxEntry } from "../types";

const STALE_REVIEW_MS = 7 * 24 * 60 * 60 * 1000;

function accentClass(runtimeState: string | null) {
  if (runtimeState === "waiting_human_review") return "inbox-row-accent";
  if (runtimeState === "completed") return "inbox-row-accent inbox-row-accent-completed";
  if (runtimeState === "aborted") return "inbox-row-accent inbox-row-accent-aborted";
  return "inbox-row-accent inbox-row-accent-default";
}

function badgeClass(runtimeState: string | null) {
  if (runtimeState === "waiting_human_review") return "badge badge-waiting";
  if (runtimeState === "completed") return "badge badge-completed";
  if (runtimeState === "aborted") return "badge badge-aborted";
  return "badge badge-default";
}

function isStaleReview(item: ReviewInboxEntry) {
  if (!item.updated_at) return true;
  const ts = Date.parse(item.updated_at);
  if (Number.isNaN(ts)) return true;
  return Date.now() - ts > STALE_REVIEW_MS;
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
      <div className={accentClass(item.runtime_state)} />
      <div className="inbox-row-body">
        <div className="inbox-row-top">
          <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
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
  const [showArchived, setShowArchived] = useState(false);
  const activeItems = items.filter((item) => !isStaleReview(item));
  const archivedItems = items.filter((item) => isStaleReview(item));

  return (
    <div style={{ display: "grid", gap: 20 }}>
      {/* page header */}
      <div className="page-header">
        <p className="page-header-eyebrow">Human-in-the-loop</p>
        <h1 className="page-header-title">Review Inbox</h1>
        <p className="page-header-sub">
          Active threads waiting for operator review. Polling every 2&nbsp;seconds.
        </p>
      </div>

      {/* stats bar */}
      <div className="stat-row">
        <div className="stat-item">
          <span className="stat-label">Active pending</span>
          <span className="stat-value">{loading ? "—" : activeItems.length}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Archived pending</span>
          <span className="stat-value">{loading ? "—" : archivedItems.length}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Total loaded</span>
          <span className="stat-value">{loading ? "—" : items.length}</span>
        </div>
      </div>

      {error ? <div className="alert alert-error">{error}</div> : null}

      {/* active queue */}
      <div className="card">
        <div className="card-header">
          <h2 className="card-title">Active queue</h2>
          <span className="badge badge-waiting">{activeItems.length} pending</span>
        </div>
        <div className="card-body">
          {loading && activeItems.length === 0 ? (
            <div className="skeleton" style={{ height: 72 }} />
          ) : null}

          {!loading && activeItems.length === 0 ? (
            <div className="empty-state">
              <span className="empty-state-icon">✓</span>
              No pending reviews in the active queue.
            </div>
          ) : null}

          <div className="inbox-list">
            {activeItems.map((item) => (
              <InboxRow key={item.thread_id} item={item} />
            ))}
          </div>
        </div>
      </div>

      {/* archive */}
      {archivedItems.length > 0 ? (
        <div className="archive-section">
          <div className="archive-toggle-bar">
            <span className="archive-toggle-label">
              Archived — {archivedItems.length} older pending run{archivedItems.length !== 1 ? "s" : ""} (&gt;7 days)
            </span>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => setShowArchived((v) => !v)}
            >
              {showArchived ? "Hide" : "Show"}
            </button>
          </div>
          {showArchived ? (
            <div className="inbox-list" style={{ marginTop: 8 }}>
              {archivedItems.map((item) => (
                <InboxRow key={item.thread_id} item={item} />
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

