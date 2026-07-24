import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchReviewDetail, resumeReviewThread } from "../api";
import { DecisionForm } from "../components/DecisionForm";
import { KeyValueGrid } from "../components/KeyValueGrid";
import { usePolledResource } from "../hooks";
import type { ReviewDetailResponse } from "../types";

function valueToString(value: unknown) {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function stateClass(state: string) {
  if (state === "waiting_human_review") return "badge badge-waiting";
  if (state === "completed") return "badge badge-completed";
  if (state === "aborted") return "badge badge-aborted";
  return "badge badge-default";
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
          Back to inbox
        </Link>
        <div className="review-header-main">
          <div>
            <p className="page-header-eyebrow">Human review</p>
            <h1 className="review-title">{taskGoal ?? "Review pending task"}</h1>
          </div>
          <div className="review-header-status">
            {snapshot ? (
              <span className={stateClass(snapshot.runtime_state)}>{snapshot.runtime_state}</span>
            ) : null}
            {snapshot?.resume_state ? (
              <span className="badge badge-neutral">resume: {snapshot.resume_state}</span>
            ) : null}
          </div>
        </div>
        <div className="review-thread-id">{decodedThreadId}</div>
      </section>

      {resource.error ? <div className="alert alert-error">{resource.error}</div> : null}
      {operatorFeedback ? <div className="alert alert-success">{operatorFeedback}</div> : null}
      {resource.loading && !detail ? <div className="skeleton" style={{ height: 120 }} /> : null}

      {detail && snapshot ? (
        <>
          {draftBody ? (
            <section className="card card-info">
              <div className="card-header">
                <h2 className="card-title">Produced text</h2>
                <span className="badge badge-info">
                  {pendingRequest?.external_communication_draft?.channel ?? "draft"}
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
                setDetailOverride(nextDetail);
                setOperatorFeedback(nextDetail.operator_feedback ?? null);
                await resource.refresh();
              }}
            />
          ) : (
            <section className="card">
              <div className="card-body muted-panel">No pending review action for this thread.</div>
            </section>
          )}

          {externalExecution ? (
            <section className="card">
              <div className="card-header">
                <h2 className="card-title">External operation</h2>
                <span className={stateClass(externalExecution.status ?? snapshot.runtime_state)}>
                  {externalExecution.status ??
                    (externalExecution.execution_intent === "proposal_only"
                      ? "draft only"
                      : "not executed")}
                </span>
              </div>
              <div className="card-body-sm">
                <KeyValueGrid
                  title={
                    externalExecution.execution_intent === "proposal_only"
                      ? "Approved artifact — no external action"
                      : "Governed execution receipt"
                  }
                  values={[
                    { label: "Intent", value: externalExecution.execution_intent },
                    { label: "Capability", value: externalExecution.capability_id ?? null },
                    { label: "Status", value: externalExecution.status ?? null },
                    { label: "Digest", value: externalExecution.payload_digest ?? null },
                    { label: "Connector", value: externalExecution.connector_id ?? null },
                    {
                      label: "External reference",
                      value: externalExecution.external_reference ?? null,
                    },
                    { label: "Error", value: externalExecution.error_code ?? null },
                    { label: "Summary", value: externalExecution.summary ?? null },
                  ]}
                />
              </div>
            </section>
          ) : null}

          <details className="technical-details">
            <summary className="technical-details-summary">Technical details</summary>
            <div className="technical-details-body">
              <KeyValueGrid
                title="Review request"
                values={[
                  { label: "Request ID", value: pendingRequest?.request_id ?? null },
                  { label: "Review step", value: snapshot.current_plan_step?.step_id ?? null },
                  { label: "Review objective", value: snapshot.current_plan_step?.objective ?? null },
                  { label: "Review summary", value: pendingRequest?.review_summary ?? null },
                  {
                    label: "Allowed decisions",
                    value: pendingRequest?.allowed_decisions?.join(", ") ?? null,
                  },
                ]}
              />
              <KeyValueGrid
                title="Runtime summary"
                values={[
                  { label: "Thread", value: detail.thread_id },
                  { label: "State", value: snapshot.runtime_state },
                  { label: "Resume state", value: snapshot.resume_state ?? null },
                  { label: "Pending review", value: snapshot.has_pending_review ? "yes" : "no" },
                  { label: "Terminal", value: snapshot.is_terminal ? "yes" : "no" },
                  { label: "Reason codes", value: snapshot.reason_codes.join(", ") || null },
                ]}
              />
              {snapshot.current_plan_step ? (
                <KeyValueGrid
                  title="Current plan step"
                  values={[
                    { label: "Step ID", value: snapshot.current_plan_step.step_id },
                    { label: "Status", value: snapshot.current_plan_step.status },
                    { label: "Objective", value: snapshot.current_plan_step.objective },
                    { label: "Expected outcome", value: snapshot.current_plan_step.expected_outcome },
                    { label: "Rationale", value: snapshot.current_plan_step.rationale },
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
                title="Oversight"
                values={Object.entries((oversight ?? {}) as Record<string, unknown>).map(
                  ([label, value]) => ({ label, value: valueToString(value) }),
                )}
              />
              <KeyValueGrid
                title="External communication"
                values={Object.entries(
                  (snapshot.external_communication ?? {}) as Record<string, unknown>,
                ).map(([label, value]) => ({ label, value: valueToString(value) }))}
              />
              <KeyValueGrid
                title="External execution"
                values={Object.entries(
                  (snapshot.external_execution ?? {}) as Record<string, unknown>,
                ).map(([label, value]) => ({ label, value: valueToString(value) }))}
              />
              <section className="card">
                <div className="card-header">
                  <h2 className="card-title">Raw snapshot</h2>
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
