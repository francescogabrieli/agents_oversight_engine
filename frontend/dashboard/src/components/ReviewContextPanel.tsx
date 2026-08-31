import { useState } from "react";
import type { DashboardPillarDiagnostic, DashboardSnapshot } from "../types";
import { humanReviewDecisionLabel } from "./humanReviewPresentation";
import { KeyValueGrid } from "./KeyValueGrid";
import { reasonCodeChipLabel, reasonCodeExplanation, reasonCodeLabel } from "./reasonCodePresentation";
import { runtimeStateLabel } from "./runtimeStatePresentation";

type ReviewContextPanelProps = {
  taskGoal: string | null;
  snapshot: DashboardSnapshot;
};

const PILLAR_LABELS: Record<string, string> = {
  evidence: "evidenze",
  trajectory: "traiettoria",
};

const LEVEL_LABELS: Record<string, string> = {
  unscored: "non valutato",
  nominal: "nominale",
  degraded: "degradato",
  critical: "critico",
};

function pillarLabel(name: string) {
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

// One plain-language sentence describing whether/why this pillar matters here.
function pillarNarrative(diagnostic: DashboardPillarDiagnostic): string {
  const label = pillarLabel(diagnostic.pillar_name);
  if (!diagnostic.required) {
    return `La verifica delle ${label} non è obbligatoria in questo caso.`;
  }
  const level = levelLabel(diagnostic.level);
  const levelPart = level ? `, livello attuale ${level}` : "";
  return `La verifica delle ${label} è obbligatoria${levelPart} ed è tra i motivi della richiesta di revisione.`;
}

// Render one active or inactive pillar without exposing the full policy payload up front.
function PillarDiagnostic({ diagnostic }: { diagnostic: DashboardPillarDiagnostic }) {
  const facts = Object.entries(diagnostic.facts).map(([label, value]) => ({
    label: factLabel(label),
    value: factValue(value),
  }));

  return (
    <article className="review-diagnostic">
      <div className="review-diagnostic-header">
        <p className="review-diagnostic-title">{capitalize(pillarLabel(diagnostic.pillar_name))}</p>
        <span className={diagnostic.required ? "badge badge-waiting" : "badge badge-neutral"}>
          {diagnostic.required ? "obbligatoria" : "non richiesta"}
        </span>
      </div>
      <p className="review-diagnostic-summary">{pillarNarrative(diagnostic)}</p>
      {diagnostic.summary ? <p className="review-diagnostic-summary">{diagnostic.summary}</p> : null}
      {facts.length > 0 ? (
        <details className="technical-details">
          <summary className="technical-details-summary">Mostra i dati osservati</summary>
          <div className="technical-details-body">
            <KeyValueGrid title="Dati osservati dalla policy" values={facts} />
            {diagnostic.reason_codes.length > 0 ? (
              <div className="review-reason-chips">
                {diagnostic.reason_codes.map((code) => (
                  <span className="action-chip" key={code} title={code}>
                    {reasonCodeChipLabel(code)}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        </details>
      ) : null}
    </article>
  );
}

// Present the decision boundary for every HITL reason, including non-external reviews.
export function ReviewContextPanel({ taskGoal, snapshot }: ReviewContextPanelProps) {
  const [showTechnical, setShowTechnical] = useState(false);
  const humanReview = snapshot.human_review;
  const pendingRequest = humanReview?.pending_request;
  const governance = snapshot.governance;
  const rawReason =
    governance?.selected_reason ??
    governance?.summary ??
    pendingRequest?.review_summary ??
    humanReview?.review_summary ??
    null;
  const governanceSummary = governance?.summary;
  const reasonCodes = pendingRequest?.reason_codes ?? snapshot.reason_codes;
  // The primary code is what OPA actually selected as the deciding reason; everything
  // else riding on the same decision is supporting detail, shown in the technical view.
  const primaryReasonCode = reasonCodes[0] ?? null;
  const clearExplanation = primaryReasonCode ? reasonCodeExplanation(primaryReasonCode) : null;
  const reasonHeading = primaryReasonCode ? reasonCodeLabel(primaryReasonCode) : null;
  // Fall back to the raw governance text only for a reason code with no Italian
  // mapping yet, so nothing silently disappears from the primary view.
  const selectedReason =
    clearExplanation ?? rawReason ?? "Il sistema richiede una decisione dell'operatore prima di proseguire.";
  const judgeFindings = pendingRequest?.judge_findings ?? humanReview?.judge_findings ?? [];
  const selectedEvidenceReferences = humanReview?.selected_evidence_references ?? [];
  const allowedDecisions =
    pendingRequest?.allowed_decisions ?? humanReview?.allowed_decisions ?? [];
  const currentStepObjective = snapshot.current_plan_step?.objective ?? null;
  // The pillar reports each flagged value with the sentence it was written in, so the
  // reviewer judges the claim in context instead of a bare token. Rendered above the
  // technical details because it is the substance of this decision, not a diagnostic.
  const flaggedLiterals = parseFlaggedLiterals(
    humanReview?.evidence_diagnostic?.facts?.unanchored_literal_excerpts,
  );
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
            Perché serve una decisione dell'operatore
          </h2>
          <p className="card-caption">
            Cosa sta facendo l'attività, cosa richiede la tua approvazione e perché.
          </p>
        </div>
        <span className="badge badge-waiting">decisione richiesta</span>
      </div>
      <div className="card-body review-context-body">
        <p className="review-context-narrative">
          {taskGoal ? <>L'attività in corso è: <strong>{taskGoal}</strong> </> : null}
          {currentStepObjective ? <>Il passo attuale consiste nel: {currentStepObjective} </> : null}
          Il sistema si è fermato perché serve la tua decisione prima di continuare.
        </p>

        {reasonHeading ? <p className="review-context-reason-label">{reasonHeading}</p> : null}
        <div className="review-context-reason">{selectedReason}</div>

        {flaggedLiterals.length > 0 ? (
          <article className="flagged-values" aria-label="Valori segnalati">
            <p className="review-diagnostic-title">
              Valori segnalati — {flaggedLiterals.length}
            </p>
            <p className="review-context-summary" style={{ margin: 0 }}>
              Il controllo automatico non ha riconosciuto questi valori nelle fonti. Per
              ciascuno vedi cosa ha scritto l'agente e cosa dicono davvero le fonti.
            </p>
            <ul className="flagged-values-list">
              {flaggedLiterals.map((item) => (
                <li className="flagged-value-item" key={item.value}>
                  <code className="flagged-value-token">{item.value}</code>
                  {item.excerpt ? (
                    <p className="flagged-value-excerpt">
                      <span className="flagged-value-role">Scritto dall'agente:</span> {item.excerpt}
                    </p>
                  ) : null}
                  {item.sourceMatches.length > 0 ? (
                    item.sourceMatches.map((match, index) => (
                      <p className="flagged-value-excerpt" key={`${item.value}-${index}`}>
                        <span className="flagged-value-role flagged-value-role-source">
                          Nella fonte:
                        </span>{" "}
                        {match.excerpt}
                        {match.locator ? (
                          <span className="review-evidence-locator">{match.locator}</span>
                        ) : null}
                      </p>
                    ))
                  ) : (
                    <p className="flagged-value-excerpt flagged-value-nomatch">
                      Nessun riscontro di questo valore in nessuna fonte selezionata.
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </article>
        ) : null}

        {judgeFindings.length > 0 ? (
          <article className="review-diagnostic" aria-label="Osservazioni del giudice automatico">
            <p className="review-diagnostic-title">Cosa ha notato la verifica automatica</p>
            <ul className="review-narrative-list">
              {judgeFindings.map((finding, index) => (
                <li key={`${finding}-${index}`}>{finding}</li>
              ))}
            </ul>
          </article>
        ) : null}

        {selectedEvidenceReferences.length > 0 ? (
          <article className="review-diagnostic" aria-label="Fonti selezionate">
            <p className="review-diagnostic-title">Fonti alla base della proposta</p>
            {selectedEvidenceReferences.map((reference) => (
              <div className="review-context-summary" key={reference.observation_id}>
                <div>{reference.content_excerpt}</div>
                {reference.locator ? <div className="review-evidence-locator">{reference.locator}</div> : null}
              </div>
            ))}
          </article>
        ) : null}

        {allowedDecisions.length > 0 ? (
          <p className="review-context-summary">
            Puoi scegliere tra: {allowedDecisions.map(humanReviewDecisionLabel).join(", ")}.
          </p>
        ) : null}

        {diagnostics.length > 0 ? (
          <div className="review-diagnostics-grid">
            {diagnostics.map((diagnostic) => (
              <PillarDiagnostic key={diagnostic.pillar_name} diagnostic={diagnostic} />
            ))}
          </div>
        ) : null}

        <details
          className="technical-details"
          open={showTechnical}
          onToggle={(event) => setShowTechnical((event.target as HTMLDetailsElement).open)}
        >
          <summary className="technical-details-summary">Mostra i dettagli tecnici della richiesta</summary>
          <div className="technical-details-body">
            <KeyValueGrid
              title="Confini della decisione"
              values={[
                { label: "Passo attuale", value: currentStepObjective },
                {
                  label: "Stato di ripresa",
                  value: runtimeStateLabel(
                    pendingRequest?.resume_state ?? humanReview?.resume_state ?? snapshot.resume_state,
                  ),
                },
                {
                  label: "Decisioni consentite",
                  value: allowedDecisions.map(humanReviewDecisionLabel).join(", ") || null,
                },
                { label: "Codici motivo (governance)", value: reasonCodes.join(", ") || null },
                { label: "Motivo selezionato (audit, originale)", value: rawReason },
                {
                  label: "Sintesi tecnica (audit)",
                  value: governanceSummary && governanceSummary !== rawReason ? governanceSummary : null,
                },
              ]}
            />
          </div>
        </details>
      </div>
    </section>
  );
}
