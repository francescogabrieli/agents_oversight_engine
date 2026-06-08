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

function prettyMetadata(metadata: Record<string, unknown>) {
  return JSON.stringify(metadata, null, 2);
}

export function DecisionForm({ detail, onSubmit }: DecisionFormProps) {
  const pendingRequest =
    detail.pending_human_review ?? detail.dashboard_snapshot?.human_review?.pending_request;
  const allowedDecisions = pendingRequest?.allowed_decisions ?? [];
  const draft = pendingRequest?.external_communication_draft;
  const [decisionType, setDecisionType] = useState<string>(
    allowedDecisions[0] ?? "approve_continue",
  );
  const [reviewerId, setReviewerId] = useState("operator-local");
  const [note, setNote] = useState("");
  const [clarificationText, setClarificationText] = useState("");
  const [draftBody, setDraftBody] = useState(draft?.body ?? "");
  const [draftMetadata, setDraftMetadata] = useState(prettyMetadata(draft?.metadata ?? {}));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDecisionType(allowedDecisions[0] ?? "approve_continue");
  }, [allowedDecisions]);

  useEffect(() => {
    setDraftBody(draft?.body ?? "");
    setDraftMetadata(prettyMetadata(draft?.metadata ?? {}));
  }, [draft]);

  if (!pendingRequest) {
    return null;
  }

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
        clarification_text:
          decisionType === "ask_clarification" ? clarificationText || undefined : undefined,
      };

      if (decisionType === "modify_and_continue") {
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
      }

      await onSubmit(payload);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Unknown error.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="panel-card panel-card-accent">
      <div className="card-body gap-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="panel-eyebrow">Operator Review</p>
            <h2 className="panel-title">Review and resume</h2>
            <p className="text-sm text-slate-600">
              Submit one typed decision against request <code>{pendingRequest.request_id}</code>.
            </p>
          </div>
          <div className="badge badge-warning badge-outline px-4 py-3 font-semibold">
            {allowedDecisions.length} allowed decisions
          </div>
        </div>

        <label className="form-control">
          <span className="label-text font-semibold">Decision</span>
          <select
            className="select select-bordered mt-2"
            value={decisionType}
            onChange={(event) => setDecisionType(event.target.value)}
          >
            {allowedDecisions.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>

        <div className="grid gap-4 md:grid-cols-2">
          <label className="form-control">
            <span className="label-text font-semibold">Reviewer</span>
            <input
              className="input input-bordered mt-2"
              value={reviewerId}
              onChange={(event) => setReviewerId(event.target.value)}
            />
          </label>
          <label className="form-control">
            <span className="label-text font-semibold">Note</span>
            <input
              className="input input-bordered mt-2"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </label>
        </div>

        {decisionType === "ask_clarification" ? (
          <label className="form-control">
            <span className="label-text font-semibold">Clarification text</span>
            <textarea
              className="textarea textarea-bordered mt-2 min-h-28"
              value={clarificationText}
              onChange={(event) => setClarificationText(event.target.value)}
            />
          </label>
        ) : null}

        {decisionType === "modify_and_continue" && draft ? (
          <div className="grid gap-4 rounded-lg border border-slate-200 bg-slate-50 p-4">
            <label className="form-control">
              <span className="label-text font-semibold">External draft body</span>
              <textarea
                className="textarea textarea-bordered mt-2 min-h-56 font-mono text-sm"
                value={draftBody}
                onChange={(event) => setDraftBody(event.target.value)}
              />
            </label>
            <label className="form-control">
              <span className="label-text font-semibold">Draft metadata</span>
              <textarea
                className="textarea textarea-bordered mt-2 min-h-28 font-mono text-xs"
                value={draftMetadata}
                onChange={(event) => setDraftMetadata(event.target.value)}
              />
            </label>
          </div>
        ) : null}

        {error ? <div className="alert alert-error text-sm">{error}</div> : null}

        <button className="btn btn-primary self-start font-semibold" disabled={submitting} onClick={submit}>
          {submitting ? "Submitting..." : "Submit decision"}
        </button>
      </div>
    </section>
  );
}
