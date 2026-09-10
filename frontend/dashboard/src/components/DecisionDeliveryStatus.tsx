// Distinguish queue delivery from completion of the governed runtime task.
export function DecisionDeliveryStatus({ status, ownerAvailable }: { status?: string | null; ownerAvailable?: boolean }) {
  const labels: Record<string, string> = {
    pending: "Decisione registrata, in attesa di essere presa in carico dal runtime.",
    claimed: "Decisione presa in carico dal runtime; elaborazione in corso.",
    consumed: "Decisione recepita dal runtime. Questo non attesta il completamento del task o dell'operazione esterna.",
    failed: "La consegna o l'applicazione della decisione è fallita: controlla il terminale del runtime.",
  };
  return <div className="delivery-status" role="status">
    <p>{status ? labels[status] ?? `Stato di consegna: ${status}` : "Stato di consegna non disponibile."}</p>
    {ownerAvailable === false ? <p className="delivery-warning">Nessun processo runtime proprietario disponibile. La dashboard non riavvia la run: verifica il terminale e la procedura di recupero del thread.</p> : null}
  </div>;
}
