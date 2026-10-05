import { useState } from "react";
import type { DashboardEvidenceReference } from "../types";
import { SourceReference } from "./SourceReference";

const RETRIEVAL_LABELS: Record<string, string> = {
  search_snippet: "Anteprima di ricerca: non è il documento recuperato",
  fetched_document: "Testo recuperato dal documento",
  controlled_resource: "Risorsa controllata",
  mcp_result: "Risultato MCP",
  capability_result: "Risultato di una capacità",
};

// Keep the complete recorded actor output beside an explicitly selected source.
// Source selection survives equivalent polls; a removed selection falls back visibly.
export function EvidenceComparisonPanel({ output, proposalBody, references, required }: {
  output?: string | null;
  proposalBody?: string | null;
  references: DashboardEvidenceReference[];
  required: boolean;
}) {
  const [sourceId, setSourceId] = useState("");
  const source = references.find((item) => item.observation_id === sourceId) ?? references[0];
  const displayedText = output || proposalBody;
  const textLabel = output ? "Testo prodotto dall'agente" : proposalBody ? "Testo della proposta in revisione" : "Testo prodotto dall'agente";
  if (!required && !output && references.length === 0) return null;
  return <article className="evidence-workspace" id="review-evidence" aria-label="Confronto tra agente e fonti">
    <h3 className="review-diagnostic-title">Confronta il testo con le evidenze</h3>
    <p className="review-context-summary">Leggi cosa ha prodotto l'agente e cosa è stato effettivamente recuperato. La presenza di una fonte non conferma da sola le affermazioni.</p>
    <section className="evidence-source-overview" aria-label="Fonti alla base della proposta">
      <h4>Fonti alla base della proposta</h4>
      {source ? <>
        <label className="form-field">Fonte da confrontare ({references.length})
          <select className="form-input" value={source.observation_id} onChange={(event) => setSourceId(event.target.value)}>
            {references.map((item, index) => <option key={item.observation_id} value={item.observation_id}>
              {index + 1}. {item.locator || item.observation_id}
            </option>)}
          </select>
        </label>
        <div className="evidence-source-meta">
          <p className="evidence-source-id">Riferimento: {source.observation_id}</p>
          {source.locator ? <SourceReference locator={source.locator} /> : <p className="literal-review-help">Indirizzo della fonte non disponibile.</p>}
          {source.retrieval_kind ? <p className="literal-review-help">{RETRIEVAL_LABELS[source.retrieval_kind] ?? source.retrieval_kind}</p> : null}
          {source.source_timestamp ? <p className="literal-review-help">Data registrata della fonte: {source.source_timestamp}</p> : null}
          <p className="literal-review-help">{source.content_truncated === true
            ? "Estratto parziale: il testo registrato supera il limite della dashboard (12.000 caratteri). Apri la fonte per proseguire la verifica."
            : source.content_truncated === false
              ? "Tutto il testo registrato per questa osservazione. Il documento originale potrebbe contenere altro testo."
              : "Estratto disponibile nello snapshot: la completezza non è indicata. Non è il documento completo."}</p>
        </div>
      </> : <p className="evidence-empty">Nessuna fonte selezionata disponibile nello snapshot. Questo non dimostra che l'agente non abbia raccolto altre fonti.</p>}
    </section>
    <div className="evidence-comparison-grid">
      <section className="evidence-column" aria-label={textLabel}>
        <h4>{textLabel}</h4>
        {displayedText ? <pre className="produced-text evidence-scroll" tabIndex={0} aria-label={textLabel}>{displayedText}</pre>
          : <p className="evidence-empty">Testo prodotto non disponibile in questo snapshot. Il messaggio dell'agente e gli eventuali estratti puntuali sono riportati nella revisione.</p>}
      </section>
      <section className="evidence-column" aria-label="Estratto della fonte selezionata">
        <h4>Estratto della fonte selezionata</h4>
        {source ? <div key={source.observation_id} className="evidence-scroll" tabIndex={0} role="region" aria-label="Estratto della fonte selezionata">
          {source.content_excerpt?.trim() ? <blockquote className="review-source-excerpt">{source.content_excerpt}</blockquote>
            : <p className="evidence-empty">{source.observation_available === false
              ? "Riferimento selezionato non risolto: l'osservazione non è disponibile. Non è possibile confrontarne il contenuto."
              : "Estratto non disponibile: questa fonte non contiene testo consultabile nello snapshot."}</p>}
        </div> : <p className="evidence-empty">Nessuna fonte selezionata disponibile nello snapshot. Questo non dimostra che l'agente non abbia raccolto altre fonti.</p>}
      </section>
    </div>
  </article>;
}
