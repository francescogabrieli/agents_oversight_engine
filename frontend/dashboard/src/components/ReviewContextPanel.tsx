import type { DashboardPillarDiagnostic, DashboardSnapshot } from "../types";
import { humanReviewDecisionLabel } from "./humanReviewPresentation";
import { KeyValueGrid } from "./KeyValueGrid";

type ReviewContextPanelProps = {
  taskGoal: string | null;
  snapshot: DashboardSnapshot;
};

// Convert bounded policy fact names into readable operator labels.
function factLabel(value: string) {
  return value
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

// Preserve exact diagnostic values while keeping common facts easy to scan.
function factValue(value: unknown) {
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object") return JSON.stringify(value);
  return value === null || value === undefined ? null : String(value);
}

// Render one active or inactive pillar without exposing the full policy payload.
function PillarDiagnostic({
  diagnostic,
}: {
  diagnostic: DashboardPillarDiagnostic;
}) {
  const status = diagnostic.required ? "required" : diagnostic.mode ?? "not required";
  const facts = Object.entries(diagnostic.facts).map(([label, value]) => ({
    label: factLabel(label),
    value: factValue(value),
  }));

  return (
    <article className="review-diagnostic">
      <div className="review-diagnostic-header">
        <div>
          <p className="review-diagnostic-title">{factLabel(diagnostic.pillar_name)}</p>
          {diagnostic.summary ? (
            <p className="review-diagnostic-summary">{diagnostic.summary}</p>
          ) : null}
        </div>
        <span
          className={diagnostic.required ? "badge badge-waiting" : "badge badge-neutral"}
        >
          {status}
        </span>
      </div>
      {diagnostic.level ? (
        <div className="review-diagnostic-level">Level: {diagnostic.level}</div>
      ) : null}
      {diagnostic.reason_codes.length > 0 ? (
        <div className="review-reason-chips">
          {diagnostic.reason_codes.map((code) => (
            <span className="action-chip" key={code}>
              {code}
            </span>
          ))}
        </div>
      ) : null}
      {facts.length > 0 ? <KeyValueGrid title="Observed facts" values={facts} /> : null}
    </article>
  );
}

// Present the decision boundary for every HITL reason, including non-external reviews.
export function ReviewContextPanel({ taskGoal, snapshot }: ReviewContextPanelProps) {
  const humanReview = snapshot.human_review;
  const pendingRequest = humanReview?.pending_request;
  const governance = snapshot.governance;
  const selectedReason =
    governance?.selected_reason ??
    governance?.summary ??
    pendingRequest?.review_summary ??
    humanReview?.review_summary ??
    "The runtime requires an operator decision.";
  const governanceSummary = governance?.summary;
  const reasonCodes = pendingRequest?.reason_codes ?? snapshot.reason_codes;
  const allowedDecisions =
    pendingRequest?.allowed_decisions ?? humanReview?.allowed_decisions ?? [];
  const resumeState =
    pendingRequest?.resume_state ?? humanReview?.resume_state ?? snapshot.resume_state;
  const diagnostics = [
    humanReview?.evidence_diagnostic,
    humanReview?.trajectory_diagnostic,
  ].filter(
    (diagnostic): diagnostic is DashboardPillarDiagnostic =>
      diagnostic !== null && diagnostic !== undefined,
  );

  return (
    <section
      className="card card-accent review-context-card"
      aria-labelledby="review-context-title"
    >
      <div className="card-header">
        <div>
          <h2 className="card-title" id="review-context-title">
            Why human review is required
          </h2>
          <p className="card-caption">
            Current task, policy reason, and observed oversight facts.
          </p>
        </div>
        <span className="badge badge-waiting">decision required</span>
      </div>
      <div className="card-body review-context-body">
        <div className="review-context-reason">{selectedReason}</div>
        {governanceSummary && governanceSummary !== selectedReason ? (
          <p className="review-context-summary">{governanceSummary}</p>
        ) : null}

        {reasonCodes.length > 0 ? (
          <div className="review-reason-chips" aria-label="Governance reason codes">
            {reasonCodes.map((code) => (
              <span className="action-chip" key={code}>
                {code}
              </span>
            ))}
          </div>
        ) : null}

        <KeyValueGrid
          title="Decision boundary"
          values={[
            { label: "Task", value: taskGoal },
            { label: "Current step", value: snapshot.current_plan_step?.objective ?? null },
            { label: "Resume state", value: resumeState ?? null },
            {
              label: "Allowed decisions",
              value: allowedDecisions.map(humanReviewDecisionLabel).join(", ") || null,
            },
          ]}
        />

        {diagnostics.length > 0 ? (
          <div className="review-diagnostics-grid">
            {diagnostics.map((diagnostic) => (
              <PillarDiagnostic key={diagnostic.pillar_name} diagnostic={diagnostic} />
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}
