import { useState } from "react";
import type {
  ExternalCommunicationDraft,
  HumanReviewDecisionPayload,
  ReviewDetailResponse,
} from "../types";
import { humanReviewDecisionExplanation, humanReviewDecisionLabel } from "./humanReviewPresentation";

type DecisionFormProps = {
  detail: ReviewDetailResponse;
  onSubmit: (payload: HumanReviewDecisionPayload) => Promise<void>;
  disabled?: boolean;
};

function prettyMetadata(metadata: Record<string, unknown>) {
  return JSON.stringify(metadata, null, 2);
}

// Remount only when the governed request changes, never for an equivalent poll response.
export function DecisionForm(props: DecisionFormProps) {
  const request = props.detail.dashboard_snapshot?.human_review?.pending_request;
  const candidateDigest = request?.operation_payload_digest
    ?? request?.external_operation_proposal?.payload_digest
    ?? "no-candidate";
  return <DecisionFields key={`${props.detail.thread_id}:${request?.request_id ?? "no-request"}:${candidateDigest}`} {...props} />;
}

// Own all mutable operator input for one exact human-review request.
function DecisionFields({ detail, onSubmit, disabled = false }: DecisionFormProps) {
  const pendingRequest = detail.dashboard_snapshot?.human_review?.pending_request;
  const allowedDecisions = pendingRequest?.allowed_decisions ?? [];
  const draft = pendingRequest?.external_communication_draft;
  const operationProposal = pendingRequest?.external_operation_proposal;
  const [decisionType, setDecisionType] = useState<string>(
    "",
  );
  const [reviewerId, setReviewerId] = useState("operator-local");
  const [note, setNote] = useState("");
  const [clarificationText, setClarificationText] = useState("");
  const [draftBody, setDraftBody] = useState(draft?.body ?? "");
  const [recipient, setRecipient] = useState(draft?.recipient ?? "");
  const [subject, setSubject] = useState(draft?.subject ?? "");
  const [draftMetadata, setDraftMetadata] = useState(prettyMetadata(draft?.metadata ?? {}));
  const [operationPayload, setOperationPayload] = useState(
    JSON.stringify(operationProposal?.payload ?? {}, null, 2),
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!pendingRequest) return null;

  const isModify = decisionType === "modify_and_continue";
  const isClarification = decisionType === "ask_clarification";
  const isAbort = decisionType === "abort";
  const modificationAllowed = draft
    ? operationProposal?.modifiable !== false
    : operationProposal?.modifiable === true;

  const submit = async () => {
    if (submitting || disabled || !allowedDecisions.includes(decisionType)) return;
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
        throw new Error("Il contenuto di questa revisione non è modificabile.");
      }

      if (isModify && draft) {
        let parsedMetadata: Record<string, unknown> = {};
        try {
          parsedMetadata = JSON.parse(draftMetadata || "{}") as Record<string, unknown>;
          if (!parsedMetadata || Array.isArray(parsedMetadata) || typeof parsedMetadata !== "object") throw new Error();
        } catch {
          throw new Error("I metadati della comunicazione devono essere JSON valido.");
        }
        payload.external_communication_edit = {
          note: note || undefined,
          draft: {
            ...draft,
            channel: draft?.channel ?? "email",
            recipient: recipient || null,
            subject: subject || null,
            body: draftBody,
            metadata: parsedMetadata,
          } satisfies ExternalCommunicationDraft,
        };
      } else if (isModify && operationProposal) {
        let parsedPayload: Record<string, unknown>;
        try {
          parsedPayload = JSON.parse(operationPayload) as Record<string, unknown>;
          if (!parsedPayload || Array.isArray(parsedPayload) || typeof parsedPayload !== "object") throw new Error();
        } catch {
          throw new Error("I dati dell'operazione devono essere JSON valido.");
        }
        payload.external_operation_edit = {
          note: note || undefined,
          payload: {
            payload_type: operationProposal.operation_family,
            ...parsedPayload,
          },
        };
      } else if (isModify) {
        throw new Error("Questa revisione non ha una proposta esterna modificabile.");
      }

      await onSubmit(payload);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Errore sconosciuto.");
    } finally {
      setSubmitting(false);
    }
  };

  const reasonCodes = pendingRequest.reason_codes ?? detail.dashboard_snapshot?.reason_codes ?? [];
  const activeExplanation = decisionType === "approve_continue"
    ? reasonCodes.includes("external_execution_reconciliation_required")
      ? "Confermi l'esito già verificato dell'operazione. Non autorizzi un nuovo tentativo."
      : detail.dashboard_snapshot?.external_execution?.execution_intent === "proposal_only"
        ? "Approvi la prosecuzione nel perimetro di sola proposta del task: non autorizzi un invio reale."
        : reasonCodes.includes("capability_approval_required")
          ? "Autorizzi questa esatta proposta per l'esecuzione, entro i controlli del runtime. Verifica prima destinatario e contenuto."
          : "Sblocchi soltanto il controllo oggetto di questa revisione. Non equivale a un'autorizzazione di invio; possono seguire altri controlli."
    : humanReviewDecisionExplanation(decisionType);
  const cannotSubmit = disabled || submitting || !decisionType ||
    (isModify && !modificationAllowed) || (isClarification && !clarificationText.trim());

  return (
    <section className="card card-accent" id="review-decision" aria-label="La tua decisione">
      <div className="card-header">
        <div>
          <h2 className="card-title">La tua decisione</h2>
          <p className="card-caption">Nessuna scelta automatica: seleziona un'azione e conferma l'invio.</p>
        </div>
      </div>
      <div className="card-body review-actions-body">
        <div className="decision-button-grid">
          {allowedDecisions.map((decision) => (
            <button
              key={decision}
              type="button"
              disabled={submitting || disabled || (decision === "modify_and_continue" && !modificationAllowed)}
              aria-pressed={decision === decisionType}
              onClick={() => setDecisionType(decision)}
              className={[
                "btn-decision",
                decision === decisionType ? "active" : "",
                decision === "abort" ? "abort" : "",
              ].filter(Boolean).join(" ")}
            >
              {humanReviewDecisionLabel(decision)}
            </button>
          ))}
        </div>

        {activeExplanation ? <p className="review-context-summary">{activeExplanation}</p> : null}
        {!modificationAllowed && allowedDecisions.includes("modify_and_continue") ? <p>Modifica non disponibile: questa richiesta non contiene una proposta tipizzata modificabile.</p> : null}

        {isClarification ? (
          <div className="form-field">
            <label className="form-label" htmlFor="clarification">Chiarimento richiesto (obbligatorio)</label>
            <textarea
              className="form-textarea"
              id="clarification"
              style={{ minHeight: 96 }}
              value={clarificationText}
              onChange={(event) => setClarificationText(event.target.value)}
              placeholder="Scrivi l'indicazione da registrare per questa richiesta..."
            />
          </div>
        ) : null}

        {isModify && modificationAllowed && draft ? (
          <div className="edit-draft-panel">
            <p className="section-label" style={{ margin: 0 }}>Modifica il testo prodotto</p>
            <label className="form-field">Destinatario
              <input className="form-input" value={recipient} onChange={(event) => setRecipient(event.target.value)} />
            </label>
            <label className="form-field">Oggetto
              <input className="form-input" value={subject} onChange={(event) => setSubject(event.target.value)} />
            </label>
            <div className="form-field">
              <textarea
                className="form-textarea form-textarea-mono"
                aria-label="Corpo modificato"
                style={{ minHeight: 260 }}
                value={draftBody}
                onChange={(event) => setDraftBody(event.target.value)}
              />
            </div>
          </div>
        ) : null}

        {isModify && modificationAllowed && !draft && operationProposal ? (
          <div className="edit-draft-panel">
            <p className="section-label" style={{ margin: 0 }}>Modifica i dati dell'operazione</p>
            <div className="form-field">
              <textarea
                aria-label="Dati modificati dell'operazione"
                className="form-textarea form-textarea-mono"
                style={{ minHeight: 260 }}
                value={operationPayload}
                onChange={(event) => setOperationPayload(event.target.value)}
              />
            </div>
          </div>
        ) : null}

        {error ? <div className="alert alert-error">{error}</div> : null}
        <label className="form-field">Nota per il runtime e per l'audit (facoltativa)
          <textarea className="form-textarea" value={note} onChange={(event) => setNote(event.target.value)}
            placeholder="Motiva la decisione o indica cosa correggere / ricercare." />
        </label>
        {!decisionType ? <p role="status">Seleziona una decisione per abilitare l'invio.</p> : null}
        {disabled ? <p role="alert">Dati non aggiornati: l'invio è sospeso fino al ripristino della connessione.</p> : null}

        <div className="action-submit-row">
          <button
            className="btn btn-primary"
            disabled={cannotSubmit}
            onClick={submit}
            style={isAbort ? { background: "var(--danger)", borderColor: "var(--danger)" } : undefined}
          >
            {submitting ? "Invio in corso..." : decisionType ? `Invia decisione: ${humanReviewDecisionLabel(decisionType)}` : "Invia decisione"}
          </button>

          <details className="technical-details" style={{ flex: 1 }}>
            <summary className="technical-details-summary" style={{ borderTop: "none", paddingLeft: 8 }}>
              Opzioni avanzate
            </summary>
            <div className="technical-details-body">
              <div className="operator-options-grid">
                <div className="form-field">
                  <label className="form-label" htmlFor="reviewer-id">ID revisore</label>
                  <input
                    className="form-input"
                    id="reviewer-id"
                    value={reviewerId}
                    onChange={(event) => setReviewerId(event.target.value)}
                  />
                </div>
              </div>
              {isModify && draft ? (
                <div className="form-field">
                  <label className="form-label" htmlFor="draft-metadata">
                    Metadati <span style={{ fontWeight: 400, color: "var(--text-muted)" }}>(JSON)</span>
                  </label>
                  <textarea
                    id="draft-metadata"
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
