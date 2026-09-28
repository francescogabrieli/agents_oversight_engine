import type { DashboardPillarDiagnostic } from "../types";
import { SourceReference } from "./SourceReference";
import { LiteralExcerpt } from "./LiteralExcerpt";

type SourceMatch = { locator: string; excerpt: string };
type FlaggedLiteral = { value: string; excerpt: string; sourceMatches: SourceMatch[] };

// The excerpt list crosses the wire as untyped JSON facts; accept only well-formed
// entries so a malformed payload degrades to "no context" rather than breaking the page.
function parseSourceMatches(value: unknown): SourceMatch[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const { locator, excerpt } = entry as Record<string, unknown>;
    if (typeof excerpt !== "string" || excerpt.length === 0) return [];
    return [{ locator: typeof locator === "string" ? locator : "", excerpt }];
  });
}

// Preserve the original actor passage without inventing a sentence for old snapshots.
function parseFlaggedLiterals(value: unknown): FlaggedLiteral[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const { value: token, excerpt, source_matches } = entry as Record<string, unknown>;
    if (typeof token !== "string" || token.length === 0) return [];
    return [
      {
        value: token,
        excerpt: typeof excerpt === "string" ? excerpt : "",
        sourceMatches: parseSourceMatches(source_matches),
      },
    ];
  });
}

// Render only recorded matches; legacy tokens retain an explicit missing-context state.
export function FlaggedEvidencePanel({ diagnostic }: { diagnostic?: DashboardPillarDiagnostic | null }) {
  const recorded = parseFlaggedLiterals(diagnostic?.facts?.unanchored_literal_excerpts);
  const tokens = diagnostic?.facts?.unanchored_literals_in_selection;
  const flaggedLiterals = [...recorded];
  if (Array.isArray(tokens)) {
    for (const value of tokens) {
      if (typeof value === "string" && value && !flaggedLiterals.some((item) => item.value === value)) {
        flaggedLiterals.push({ value, excerpt: "", sourceMatches: [] });
      }
    }
  }
  return <>
        {flaggedLiterals.length > 0 ? (
          <article className="flagged-values" aria-label="Valori segnalati">
            <p className="review-diagnostic-title">
              Valori segnalati — {flaggedLiterals.length}
            </p>
            <p className="review-context-summary" style={{ margin: 0 }}>
              Verifica la frase completa: il valore deve riferirsi allo stesso soggetto,
              alla stessa unità e allo stesso periodo. La segnalazione non dimostra un errore.
            </p>
            <ul className="flagged-values-list">
              {flaggedLiterals.map((item) => (
                <li className="flagged-value-item" key={`${item.value}-${item.excerpt}`}>
                  <h3 className="literal-review-title">Valore da verificare: <code className="flagged-value-token">{item.value}</code></h3>
                  <div className="literal-comparison">
                  <section className="literal-comparison-panel" aria-label={`Testo dell'agente per ${item.value}`}>
                    <h4>1. Cosa ha scritto l'agente</h4>
                    <blockquote className="flagged-value-excerpt">
                      {item.excerpt ? <LiteralExcerpt text={item.excerpt} value={item.value} /> : "Contesto della frase non disponibile nella richiesta."}
                    </blockquote>
                    <p className="literal-review-help">Estratto registrato: eventuali «…» indicano testo omesso.</p>
                  </section>
                  <section className="literal-comparison-panel" aria-label={`Passaggi da verificare per ${item.value}`}>
                  <h4>2. Confronta con la fonte</h4>
                  <p className="literal-review-help">I passaggi proposti dal confronto automatico possono essere poco pertinenti. Non sono una conferma dell'affermazione.</p>
                  {item.sourceMatches.length > 0 ? (
                    item.sourceMatches.map((match, index) => (
                      <div className="literal-source-match" key={`${item.value}-${index}`}>
                        <p className="flagged-value-role">Passaggio da esaminare {index + 1}</p>
                        <blockquote className="flagged-value-excerpt"><LiteralExcerpt text={match.excerpt} value={item.value} /></blockquote>
                        {!match.excerpt.includes(item.value) ? <p className="literal-review-help">Il valore «{item.value}» non compare in questa forma nell'estratto. Verifica eventuali notazioni diverse o apri il documento.</p> : null}
                        {match.locator ? (
                          <div className="literal-source-link"><span>Apri la fonte di questo passaggio:</span><SourceReference locator={match.locator} /></div>
                        ) : <p className="literal-review-help">Link del passaggio non disponibile.</p>}
                      </div>
                    ))
                  ) : (
                    <p className="flagged-value-excerpt flagged-value-nomatch">
                      Nessun passaggio di confronto disponibile nel testo recuperato. Questo non prova che il valore sia assente dal documento originale.
                    </p>
                  )}
                  </section>
                  </div>
                  <div className="literal-verification-guide">
                    <strong>Come verificarlo</strong>
                    <p>Se l'estratto non basta, apri la fonte e cerca «{item.value}» con Ctrl+F (anche con una notazione diversa). Leggi il paragrafo e controlla soggetto, soglia, unità e periodo. Puoi consultare anche le fonti selezionate nel confronto qui sopra.</p>
                    <p>Se confermi l'affermazione, indica nella nota fonte e passaggio verificati. Se resta incerta, non considerare il solo numero trovato una conferma.</p>
                  </div>
                </li>
              ))}
            </ul>
          </article>
        ) : null}

  </>;
}
