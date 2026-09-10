import type { HumanReviewPendingRequest } from "../types";
import { KeyValueGrid } from "./KeyValueGrid";

// Show the exact typed proposal before the operator chooses any decision.
export function ProposalPanel({ request }: { request: HumanReviewPendingRequest }) {
  const draft = request.external_communication_draft;
  const proposal = request.external_operation_proposal;
  if (!draft && !proposal) return null;
  return (
    <section className="card card-info" aria-label="Proposta in revisione">
      <div className="card-header"><div>
        <h2 className="card-title">Proposta in revisione</h2>
        <p className="card-caption">Controlla destinatario, contenuto e ambito. L'effetto della decisione dipende dal motivo di questa revisione.</p>
      </div></div>
      <div className="card-body">
        <KeyValueGrid title="Destinazione e identità" values={[
          { label: "Canale / famiglia", value: draft?.channel ?? proposal?.operation_family },
          { label: "Destinatario", value: draft?.recipient ?? request.operation_target },
          { label: "Oggetto", value: draft?.subject },
          { label: "CC", value: draft?.cc?.join(", ") },
          { label: "BCC", value: draft?.bcc?.join(", ") },
          { label: "Riepilogo", value: request.operation_proposal_summary },
          { label: "Digest della proposta", value: request.operation_payload_digest ?? proposal?.payload_digest },
        ]} />
        {draft ? <>
          <h3>Testo prodotto</h3>
          <pre className="produced-text proposal-scroll" tabIndex={0} aria-label="Corpo della proposta">{draft.body}</pre>
        </> : <KeyValueGrid title="Dati dell'operazione (query, parametri e profilo se presenti)" values={
          Object.entries(proposal?.payload ?? {}).map(([label, value]) => ({
            label, value: typeof value === "string" ? value : JSON.stringify(value, null, 2),
          }))
        } />}
      </div>
    </section>
  );
}
