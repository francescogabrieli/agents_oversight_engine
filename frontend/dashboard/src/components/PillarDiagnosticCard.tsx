import type { DashboardPillarDiagnostic } from "../types";
import { KeyValueGrid } from "./KeyValueGrid";
import { pillarHighlights } from "./pillarSummary";
import { reasonCodeChipLabel } from "./reasonCodePresentation";

const PILLAR_LABELS: Record<string, string> = {
  evidence: "evidenze",
  trajectory: "traiettoria",
};

const LEVEL_LABELS: Record<string, string> = {
  unscored: "senza punteggio",
  observed: "osservata",
  nominal: "nominale",
  degraded: "degradato",
  critical: "critico",
};

// The excerpt list is rendered in full above the diagnostics, with each flagged value
// paired to the sentence it appeared in; repeating it here as a JSON blob in a table
// cell is the single worst offender for readability on an evidence stop.
const RAW_FACTS_RENDERED_ELSEWHERE = new Set(["unanchored_literal_excerpts"]);

export function pillarLabel(name: string) {
  return PILLAR_LABELS[name] ?? name;
}

function capitalize(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function levelLabel(level: string | null | undefined) {
  if (!level) return null;
  return LEVEL_LABELS[level.toLowerCase()] ?? level;
}

// Convert bounded policy fact names into readable operator labels.
function factLabel(value: string) {
  return value
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

// Preserve exact diagnostic values while keeping common facts easy to scan.
function factValue(value: unknown) {
  if (typeof value === "boolean") return value ? "Sì" : "No";
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object") return JSON.stringify(value);
  return value === null || value === undefined ? null : String(value);
}

// Say whether this pillar is the reason the run stopped, rather than describing every
// required pillar as if it were: on most stops only one of the two actually fired.
function pillarNarrative(diagnostic: DashboardPillarDiagnostic, isDeciding: boolean): string {
  const label = pillarLabel(diagnostic.pillar_name);
  if (!diagnostic.required) {
    return `La verifica delle ${label} non è obbligatoria in questo caso.`;
  }
  const level = levelLabel(diagnostic.level);
  const levelPart = level ? ` (livello ${level})` : "";
  if (isDeciding) {
    return `La verifica delle ${label}${levelPart} è il motivo per cui l'attività si è fermata.`;
  }
  return `Dati della verifica delle ${label}${levelPart}. Il motivo selezionato dalla governance è indicato nel riepilogo della richiesta.`;
}

type PillarDiagnosticCardProps = {
  diagnostic: DashboardPillarDiagnostic;
  // Reason codes carried by the decision that stopped the run, used only to tell the
  // deciding pillar apart from the one that merely ran.
  decisionReasonCodes: string[];
};

// Render one pillar as a short list of what it observed, with the full policy payload
// kept one click away for audit.
export function PillarDiagnosticCard({
  diagnostic,
  decisionReasonCodes,
}: PillarDiagnosticCardProps) {
  const isDeciding = diagnostic.reason_codes.some((code) => decisionReasonCodes.includes(code));
  const highlights = pillarHighlights(diagnostic.pillar_name, diagnostic.facts);
  const facts = Object.entries(diagnostic.facts)
    .filter(([label]) => !RAW_FACTS_RENDERED_ELSEWHERE.has(label))
    .map(([label, value]) => ({ label: factLabel(label), value: factValue(value) }));

  return (
    <article className="review-diagnostic">
      <div className="review-diagnostic-header">
        <p className="review-diagnostic-title">{capitalize(pillarLabel(diagnostic.pillar_name))}</p>
        <span
          className={
            isDeciding ? "badge badge-waiting" : diagnostic.required ? "badge badge-info" : "badge badge-neutral"
          }
        >
          {isDeciding ? "causa della fermata" : diagnostic.required ? "verificata" : "non richiesta"}
        </span>
      </div>
      <p className="review-diagnostic-summary">{pillarNarrative(diagnostic, isDeciding)}</p>
      {highlights.length > 0 ? (
        <dl className="pillar-highlights">
          {highlights.map((highlight) => (
            <div className={`pillar-highlight pillar-highlight-${highlight.tone}`} key={highlight.label}>
              <dt>{highlight.label}</dt>
              <dd>{highlight.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {diagnostic.reason_codes.length > 0 ? (
        <div className="review-reason-chips">
          {diagnostic.reason_codes.map((code) => (
            <span className="action-chip" key={code} title={code}>
              {reasonCodeChipLabel(code)}
            </span>
          ))}
        </div>
      ) : null}
      {facts.length > 0 ? (
        <details className="technical-details">
          <summary className="technical-details-summary">Mostra i dati osservati</summary>
          <div className="technical-details-body">
            <KeyValueGrid title="Dati osservati dalla policy" values={facts} />
            {diagnostic.summary ? <p className="review-diagnostic-summary">{diagnostic.summary}</p> : null}
          </div>
        </details>
      ) : null}
    </article>
  );
}
