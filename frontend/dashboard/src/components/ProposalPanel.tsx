import type { ExternalCommunicationDraft, HumanReviewPendingRequest } from "../types";
import { KeyValueGrid } from "./KeyValueGrid";

// Show the active proposal or the communication body retained after review closes.
export function ProposalPanel({
  request,
  persistedDraft,
}: {
  request?: HumanReviewPendingRequest | null;
  persistedDraft?: ExternalCommunicationDraft | null;
}) {
  const draft = request ? request.external_communication_draft : persistedDraft;
  const proposal = request?.external_operation_proposal;
  if (!draft && !proposal) return null;
  const isPendingReview = request != null;
  return (
    <section id="communication-body" className="card card-info" aria-label={isPendingReview ? "Proposta in revisione" : "Comunicazione registrata"}>
      <div className="card-header"><div>
        <h2 className="card-title">{isPendingReview ? "Proposta in revisione" : "Comunicazione registrata"}</h2>
        <p className="card-caption">{isPendingReview
          ? "Controlla destinatario, contenuto e ambito. L'effetto della decisione dipende dal motivo di questa revisione."
          : "Contenuto della comunicazione conservato nello snapshot della run."}</p>
      </div></div>
      <div className="card-body">
        <KeyValueGrid title="Destinazione e identità" values={[
          { label: "Canale / famiglia", value: draft?.channel ?? proposal?.operation_family },
          { label: "Destinatario", value: draft?.recipient ?? request?.operation_target },
          { label: "Oggetto", value: draft?.subject },
          { label: "CC", value: draft?.cc?.join(", ") },
          { label: "BCC", value: draft?.bcc?.join(", ") },
          { label: "Riepilogo", value: request?.operation_proposal_summary },
          { label: "Digest della proposta", value: request?.operation_payload_digest ?? proposal?.payload_digest },
        ]} />
        {draft ? <>
          <h3>{isPendingReview ? "Testo prodotto" : "Corpo della comunicazione"}</h3>
          <pre className="produced-text proposal-scroll" tabIndex={0} aria-label="Corpo della comunicazione">{draft.body}</pre>
        </> : <KeyValueGrid title="Dati dell'operazione (query, parametri e profilo se presenti)" values={
          Object.entries(proposal?.payload ?? {}).map(([label, value]) => ({
            label, value: typeof value === "string" ? value : JSON.stringify(value, null, 2),
          }))
        } />}
      </div>
    </section>
  );
}
