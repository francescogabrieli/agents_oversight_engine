import { useEffect, useState } from "react";
import type {
  ExternalCommunicationDraft,
  HumanReviewDecisionPayload,
  ReviewDetailResponse,
} from "../types";

type DecisionFormProps = {
  detail: ReviewDetailResponse;
  onSubmit: (payload: HumanReviewDecisionPayload) => Promise<void>;
};

const DECISION_LABELS: Record<string, string> = {
  approve_continue: "Approve",
  approve_continue_read_only: "Approve read-only",
  modify_and_continue: "Modify",
  request_more_evidence: "More evidence",
  replan: "Replan",
  ask_clarification: "Clarify",
  abort: "Abort",
};

function prettyMetadata(metadata: Record<string, unknown>) {
  return JSON.stringify(metadata, null, 2);
}

export function DecisionForm({ detail, onSubmit }: DecisionFormProps) {
  const pendingRequest = detail.dashboard_snapshot?.human_review?.pending_request;
  const allowedDecisions = pendingRequest?.allowed_decisions ?? [];
  // Polling recreates the payload array; compare its values so it cannot reset a user's choice.
  const allowedDecisionsKey = allowedDecisions.join("\u0000");
  const draft = pendingRequest?.external_communication_draft;
  const operationProposal = pendingRequest?.external_operation_proposal;
  const [decisionType, setDecisionType] = useState<string>(
    allowedDecisions[0] ?? "approve_continue",
  );
  const [reviewerId, setReviewerId] = useState("operator-local");
  const [note, setNote] = useState("");
  const [clarificationText, setClarificationText] = useState("");
  const [draftBody, setDraftBody] = useState(draft?.body ?? "");
  const [draftMetadata, setDraftMetadata] = useState(prettyMetadata(draft?.metadata ?? {}));
  const [operationPayload, setOperationPayload] = useState(
    JSON.stringify(operationProposal?.payload ?? {}, null, 2),
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDecisionType(allowedDecisions[0] ?? "approve_continue");
  }, [allowedDecisionsKey]);

  useEffect(() => {
    setDraftBody(draft?.body ?? "");
    setDraftMetadata(prettyMetadata(draft?.metadata ?? {}));
  }, [draft]);

  useEffect(() => {
    setOperationPayload(JSON.stringify(operationProposal?.payload ?? {}, null, 2));
  }, [operationProposal]);

  if (!pendingRequest) return null;

  const isModify = decisionType === "modify_and_continue";
  const isClarification = decisionType === "ask_clarification";
  const isAbort = decisionType === "abort";
  const modificationAllowed = draft
    ? operationProposal?.modifiable !== false
    : operationProposal?.modifiable === true;

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const payload: HumanReviewDecisionPayload = {
        thread_id: detail.thread_id,
        request_id: pendingRequest.request_id,
        decision_type: decisionType,
        reviewer_id: reviewerId,
        note: note || undefined,
        clarification_text: isClarification ? clarificationText || undefined : undefined,
      };

      if (isModify && !modificationAllowed) {
        throw new Error("The current review payload is not modifiable.");
      }

      if (isModify && draft) {
        let parsedMetadata: Record<string, unknown> = {};
        try {
          parsedMetadata = JSON.parse(draftMetadata || "{}") as Record<string, unknown>;
        } catch {
          throw new Error("External communication metadata must be valid JSON.");
        }
        payload.external_communication_edit = {
          note: note || undefined,
          draft: {
            channel: draft?.channel ?? "email",
            recipient: draft?.recipient ?? null,
            subject: draft?.subject ?? null,
            body: draftBody,
            metadata: parsedMetadata,
          } satisfies ExternalCommunicationDraft,
        };
      } else if (isModify && operationProposal) {
        let parsedPayload: Record<string, unknown>;
        try {
          parsedPayload = JSON.parse(operationPayload) as Record<string, unknown>;
        } catch {
          throw new Error("External operation payload must be valid JSON.");
        }
        payload.external_operation_edit = {
          note: note || undefined,
          payload: {
            payload_type: operationProposal.operation_family,
            ...parsedPayload,
          },
        };
      } else if (isModify) {
        throw new Error("This review has no modifiable external proposal.");
      }

      await onSubmit(payload);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Unknown error.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="card card-accent">
      <div className="card-header">
        <h2 className="card-title">Actions</h2>
      </div>
      <div className="card-body review-actions-body">
        <div className="decision-button-grid">
          {allowedDecisions.map((decision) => (
            <button
              key={decision}
              type="button"
              onClick={() => setDecisionType(decision)}
              className={[
                "btn-decision",
                decision === decisionType ? "active" : "",
                decision === "abort" ? "abort" : "",
              ].filter(Boolean).join(" ")}
            >
              {DECISION_LABELS[decision] ?? decision}
            </button>
          ))}
        </div>

        {isClarification ? (
          <div className="form-field">
            <label className="form-label">Clarification text</label>
            <textarea
              className="form-textarea"
              style={{ minHeight: 96 }}
              value={clarificationText}
              onChange={(event) => setClarificationText(event.target.value)}
              placeholder="Describe what clarification is needed..."
            />
          </div>
        ) : null}

        {isModify && !modificationAllowed ? (
          <div className="alert alert-warning">
            Modify is always available as an HITL control, but this review has no
            editable typed payload.
          </div>
        ) : null}

        {isModify && modificationAllowed && draft ? (
          <div className="edit-draft-panel">
            <p className="section-label" style={{ margin: 0 }}>Edit produced text</p>
            <div className="form-field">
              <textarea
                className="form-textarea form-textarea-mono"
                style={{ minHeight: 260 }}
                value={draftBody}
                onChange={(event) => setDraftBody(event.target.value)}
              />
            </div>
          </div>
        ) : null}

        {isModify && modificationAllowed && !draft && operationProposal ? (
          <div className="edit-draft-panel">
            <p className="section-label" style={{ margin: 0 }}>Edit typed operation payload</p>
            <div className="form-field">
              <textarea
                className="form-textarea form-textarea-mono"
                style={{ minHeight: 260 }}
                value={operationPayload}
                onChange={(event) => setOperationPayload(event.target.value)}
              />
            </div>
          </div>
        ) : null}

        {error ? <div className="alert alert-error">{error}</div> : null}

        <div className="action-submit-row">
          <button
            className="btn btn-primary"
            disabled={submitting}
            onClick={submit}
            style={isAbort ? { background: "var(--danger)", borderColor: "var(--danger)" } : undefined}
          >
            {submitting ? "Submitting..." : "Submit decision"}
          </button>

          <details className="technical-details" style={{ flex: 1 }}>
            <summary className="technical-details-summary" style={{ borderTop: "none", paddingLeft: 8 }}>
              Advanced options
            </summary>
            <div className="technical-details-body">
              <div className="operator-options-grid">
                <div className="form-field">
                  <label className="form-label">Reviewer ID</label>
                  <input
                    className="form-input"
                    value={reviewerId}
                    onChange={(event) => setReviewerId(event.target.value)}
                  />
                </div>
                <div className="form-field">
                  <label className="form-label">
                    Note <span style={{ fontWeight: 400, color: "var(--text-muted)" }}>(optional)</span>
                  </label>
                  <input
                    className="form-input"
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                    placeholder="Internal note..."
                  />
                </div>
              </div>
              {isModify && draft ? (
                <div className="form-field">
                  <label className="form-label">
                    Metadata <span style={{ fontWeight: 400, color: "var(--text-muted)" }}>(JSON)</span>
                  </label>
                  <textarea
                    className="form-textarea form-textarea-mono"
                    style={{ minHeight: 80 }}
                    value={draftMetadata}
                    onChange={(event) => setDraftMetadata(event.target.value)}
                  />
                </div>
              ) : null}
              <div className="review-request-id">{pendingRequest.request_id}</div>
            </div>
          </details>
        </div>
      </div>
    </section>
  );
}
