import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchReviewDetail, resumeReviewThread } from "../api";
import { DecisionForm } from "../components/DecisionForm";
import { KeyValueGrid } from "../components/KeyValueGrid";
import { usePolledResource } from "../hooks";
import type { ReviewDetailResponse } from "../types";

function valueToString(value: unknown) {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2);
}

export function ReviewThreadPage() {
  const { threadId = "" } = useParams();
  const decodedThreadId = decodeURIComponent(threadId);
  const resource = usePolledResource(() => fetchReviewDetail(decodedThreadId), [decodedThreadId]);
  const [detailOverride, setDetailOverride] = useState<ReviewDetailResponse | null>(null);
  const [operatorFeedback, setOperatorFeedback] = useState<string | null>(null);

  useEffect(() => {
    setDetailOverride(resource.data);
  }, [resource.data]);

  const detail = detailOverride ?? resource.data;
  const snapshot = detail?.dashboard_snapshot;

  return (
    <div className="grid gap-5">
      <section className="hero-panel">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <Link to="/" className="back-link">
              {"<-"} Back to inbox
            </Link>
            <p className="panel-eyebrow panel-eyebrow-hero mt-4">Thread detail</p>
            <h1 className="hero-title mt-2 text-[1.9rem]">{decodedThreadId}</h1>
            <p className="hero-lede mt-3 max-w-3xl">
              Polling every 2 seconds. Resume actions are applied against the persisted checkpoint
              for this thread.
            </p>
          </div>
          {snapshot ? (
            <div className="status-strip">
              <span className="status-pill status-pill-paused">{snapshot.runtime_state}</span>
              {snapshot.resume_state ? (
                <span className="status-meta">resume: {snapshot.resume_state}</span>
              ) : null}
            </div>
          ) : null}
        </div>
      </section>

      {resource.error ? <div className="alert alert-error">{resource.error}</div> : null}
      {operatorFeedback ? <div className="alert alert-success">{operatorFeedback}</div> : null}
      {resource.loading && !detail ? <div className="skeleton h-36 w-full" /> : null}

      {detail && snapshot ? (
        <>
          <div className="detail-grid">
            <div className="grid gap-4">
              <KeyValueGrid
                title="Runtime summary"
                values={[
                  { label: "Thread", value: detail.thread_id },
                  { label: "State", value: snapshot.runtime_state },
                  { label: "Pending review", value: snapshot.has_pending_review ? "yes" : "no" },
                  { label: "Terminal", value: snapshot.is_terminal ? "yes" : "no" },
                  { label: "Reason codes", value: snapshot.reason_codes.join(", ") },
                  {
                    label: "Task",
                    value: valueToString(detail.runtime_summary?.task_goal),
                  },
                ]}
              />

              {snapshot.current_plan_step ? (
                <KeyValueGrid
                  title="Current plan step"
                  values={[
                    { label: "Step ID", value: snapshot.current_plan_step.step_id },
                    { label: "Objective", value: snapshot.current_plan_step.objective },
                    { label: "Rationale", value: snapshot.current_plan_step.rationale },
                    { label: "Expected outcome", value: snapshot.current_plan_step.expected_outcome },
                    { label: "Status", value: snapshot.current_plan_step.status },
                  ]}
                />
              ) : null}

              {snapshot.human_review?.pending_request ? (
                <DecisionForm
                  detail={detail as ReviewDetailResponse}
                  onSubmit={async (payload) => {
                    const nextDetail = await resumeReviewThread(decodedThreadId, payload);
                    setDetailOverride(nextDetail);
                    setOperatorFeedback(nextDetail.operator_feedback ?? null);
                    await resource.refresh();
                  }}
                />
              ) : null}
            </div>

            <div className="grid gap-4">
              <KeyValueGrid
                title="Governance"
                values={Object.entries((snapshot.governance ?? {}) as Record<string, unknown>).map(
                  ([label, value]) => ({
                    label,
                    value: valueToString(value),
                  }),
                )}
              />

              <KeyValueGrid
                title="Oversight"
                values={Object.entries((snapshot.oversight ?? {}) as Record<string, unknown>).map(
                  ([label, value]) => ({
                    label,
                    value: valueToString(value),
                  }),
                )}
              />

              <KeyValueGrid
                title="External communication"
                values={Object.entries(
                  (snapshot.external_communication ?? {}) as Record<string, unknown>,
                ).map(([label, value]) => ({
                  label,
                  value: valueToString(value),
                }))}
              />
            </div>
          </div>

          <section className="panel-card">
            <div className="card-body">
              <h2 className="panel-title">Raw snapshot</h2>
              <pre className="raw-json-block">{JSON.stringify(detail, null, 2)}</pre>
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}
