import { useState } from "react";
import { Link } from "react-router-dom";
import { fetchReviewInbox } from "../api";
import { usePolledResource } from "../hooks";
import type { ReviewInboxEntry } from "../types";

const STALE_REVIEW_MS = 7 * 24 * 60 * 60 * 1000;

function statusClass(runtimeState: string | null) {
  if (runtimeState === "waiting_human_review") {
    return "badge-warning";
  }
  if (runtimeState === "completed") {
    return "badge-success";
  }
  if (runtimeState === "aborted") {
    return "badge-error";
  }
  return "badge-info";
}

function isStaleReview(item: ReviewInboxEntry) {
  if (!item.updated_at) {
    return true;
  }
  const updatedAt = Date.parse(item.updated_at);
  if (Number.isNaN(updatedAt)) {
    return true;
  }
  return Date.now() - updatedAt > STALE_REVIEW_MS;
}

function formatTimestamp(value: string | null) {
  if (!value) {
    return "n/a";
  }
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(timestamp);
}

export function ReviewInboxPage() {
  const { data, error, loading } = usePolledResource(fetchReviewInbox, []);
  const items = data ?? [];
  const [showArchived, setShowArchived] = useState(false);
  const activeItems = items.filter((item) => !isStaleReview(item));
  const archivedItems = items.filter((item) => isStaleReview(item));

  return (
    <div className="grid gap-5">
      <section className="hero-panel">
        <div className="space-y-4">
          <p className="panel-eyebrow panel-eyebrow-hero">Layer 2 HITL control plane</p>
          <h1 className="hero-title">Pending human reviews</h1>
          <p className="hero-lede">
            The inbox shows active `WAITING_HUMAN_REVIEW` threads first. Older pending threads are
            kept available as archived items instead of crowding the operator queue.
          </p>
          <div className="status-strip">
            <span className="status-pill status-pill-paused">paused</span>
            <span className="status-meta">Polling every 2 seconds</span>
            <span className="status-meta">{activeItems.length} active pending</span>
            <span className="status-meta">{archivedItems.length} archived pending</span>
          </div>
        </div>
      </section>

      {error ? <div className="alert alert-error">{error}</div> : null}

      <section className="panel-card">
        <div className="card-body gap-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="panel-eyebrow">Review Queue</p>
              <h2 className="panel-title">Active review inbox</h2>
              <p className="text-sm text-slate-600">
                Recent items stay in the main queue. Pending runs older than 7 days are collapsed
                into the archive below.
              </p>
            </div>
            <div className="badge badge-outline badge-warning px-4 py-3 font-semibold">
              {activeItems.length} active
            </div>
          </div>

          {loading && activeItems.length === 0 ? <div className="skeleton h-28 w-full" /> : null}

          {!loading && activeItems.length === 0 ? (
            <div className="empty-panel">
              No recent pending human reviews are currently available in the active queue.
            </div>
          ) : null}

          <div className="grid gap-4">
            {activeItems.map((item) => (
              <Link
                key={item.thread_id}
                to={`/threads/${encodeURIComponent(item.thread_id)}`}
                className="inbox-item group"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-3">
                      <span className={`badge ${statusClass(item.runtime_state)} badge-lg font-semibold`}>
                        {item.runtime_state ?? "unknown"}
                      </span>
                      <span className="text-xs uppercase tracking-[0.2em] text-slate-500">
                        {item.external_communication_phase ?? "generic-review"}
                      </span>
                    </div>
                    <h3 className="text-xl font-semibold text-slate-950 group-hover:text-blue-700">
                      {item.task_goal ?? "Untitled review task"}
                    </h3>
                  </div>
                  <div className="text-right text-xs uppercase tracking-[0.2em] text-slate-500">
                    <div>{formatTimestamp(item.updated_at)}</div>
                    <div>{item.thread_id}</div>
                  </div>
                </div>
                <p className="mt-4 max-w-3xl text-sm leading-7 text-slate-700">
                  {item.review_summary ?? "No review summary is available."}
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  {item.allowed_decisions.map((decision) => (
                    <span key={decision} className="badge badge-outline">
                      {decision}
                    </span>
                  ))}
                </div>
              </Link>
            ))}
          </div>

          {archivedItems.length > 0 ? (
            <section className="archive-panel">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="panel-eyebrow">Archive</p>
                  <h3 className="panel-title">Older pending runs</h3>
                  <p className="text-sm text-slate-600">
                    These runs are still pending in persisted artifacts, but they have not been
                    updated in more than 7 days.
                  </p>
                </div>
                <button
                  className="btn btn-sm btn-outline"
                  onClick={() => setShowArchived((value) => !value)}
                >
                  {showArchived ? "Hide archived" : `Show archived (${archivedItems.length})`}
                </button>
              </div>

              {showArchived ? (
                <div className="mt-4 grid gap-3">
                  {archivedItems.map((item) => (
                    <Link
                      key={item.thread_id}
                      to={`/threads/${encodeURIComponent(item.thread_id)}`}
                      className="inbox-item inbox-item-archived"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="badge badge-outline badge-warning">archived pending</span>
                            <span className="text-xs uppercase tracking-[0.2em] text-slate-500">
                              {item.external_communication_phase ?? "generic-review"}
                            </span>
                          </div>
                          <h4 className="mt-2 text-lg font-semibold text-slate-900">
                            {item.task_goal ?? "Untitled review task"}
                          </h4>
                        </div>
                        <div className="text-right text-xs uppercase tracking-[0.2em] text-slate-500">
                          <div>{formatTimestamp(item.updated_at)}</div>
                          <div>{item.thread_id}</div>
                        </div>
                      </div>
                    </Link>
                  ))}
                </div>
              ) : null}
            </section>
          ) : null}
        </div>
      </section>
    </div>
  );
}
