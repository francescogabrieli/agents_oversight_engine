import { useState } from "react";
import type { DashboardPillarDiagnostic, DashboardSnapshot } from "../types";
import { actorResultLabel, isPlaceholderBlockingReason } from "./actorResultPresentation";
import { humanReviewDecisionLabel } from "./humanReviewPresentation";
import { KeyValueGrid } from "./KeyValueGrid";
import { PillarDiagnosticCard } from "./PillarDiagnosticCard";
import { PriorDecisionsPanel } from "./PriorDecisionsPanel";
import { reasonCodeChipLabel, reasonCodeExplanation, reasonCodeLabel } from "./reasonCodePresentation";
import { runtimeStateLabel } from "./runtimeStatePresentation";
import { SourceReference } from "./SourceReference";
import { LiteralExcerpt } from "./LiteralExcerpt";

type ReviewContextPanelProps = {
  taskGoal: string | null;
  snapshot: DashboardSnapshot;
};

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

// OPA reports every concern that would have stopped the run, then selects one as the
// deciding reason. The others do not go away: they fire again on the next pass. An
// operator who only sees the selected reason cannot understand why approving keeps
// bringing the run back, so the still-open concerns are named here.
function parseOpenConcerns(value: unknown, primaryCodes: string[]): string[] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.keys(value as Record<string, unknown>).filter(
    (code) => !primaryCodes.includes(code),
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
  // else riding on the same decision is supporting detail, shown as chips below.
  const primaryReasonCode = reasonCodes[0] ?? null;
  const supportingReasonCodes = reasonCodes.slice(1);
  const clearExplanation = primaryReasonCode ? reasonCodeExplanation(primaryReasonCode) : null;
  const reasonHeading = primaryReasonCode ? reasonCodeLabel(primaryReasonCode) : null;
  // Fall back to the raw governance text only for a reason code with no Italian
  // mapping yet, so nothing silently disappears from the primary view.
  const selectedReason =
    clearExplanation ?? rawReason ?? "Il sistema richiede una decisione dell'operatore prima di proseguire.";
  const openConcerns = parseOpenConcerns(governance?.rejected_alternatives, reasonCodes);
  const judgeFindings = pendingRequest?.judge_findings ?? humanReview?.judge_findings ?? [];
  const selectedEvidenceReferences = humanReview?.selected_evidence_references ?? [];
  const allowedDecisions =
    pendingRequest?.allowed_decisions ?? humanReview?.allowed_decisions ?? [];
  const currentStepObjective = snapshot.current_plan_step?.objective ?? null;
  const actorHeading = actorResultLabel(pendingRequest?.actor_result_type);
  const actorSummary = pendingRequest?.actor_summary ?? null;
  const actorBlockingReason = pendingRequest?.actor_blocking_reason ?? null;
  const hasActorVoice = Boolean(actorHeading && (actorSummary || actorBlockingReason));
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

        {hasActorVoice ? (
          <article className="actor-voice" aria-label="Cosa ha detto l'agente">
            <p className="review-diagnostic-title">{actorHeading}</p>
            {actorSummary ? <p className="actor-voice-text">{actorSummary}</p> : null}
            {actorBlockingReason ? (
              <p className="actor-voice-text">
                <span className="flagged-value-role">
                  {isPlaceholderBlockingReason(actorBlockingReason)
                    ? "Motivo dichiarato (nessun motivo reale indicato):"
                    : "Motivo del blocco:"}
                </span>{" "}
                {actorBlockingReason}
              </p>
            ) : null}
          </article>
        ) : null}

        {reasonHeading ? <p className="review-context-reason-label">{reasonHeading}</p> : null}
        <div className="review-context-reason">{selectedReason}</div>

        {supportingReasonCodes.length > 0 ? (
          <div className="review-reason-chips" aria-label="Rilievi collegati">
            {supportingReasonCodes.map((code) => (
              <span className="action-chip" key={code} title={code}>
                {reasonCodeChipLabel(code)}
              </span>
            ))}
          </div>
        ) : null}

        {openConcerns.length > 0 ? (
          <article className="open-concerns" aria-label="Altre criticità ancora aperte">
            <p className="review-diagnostic-title">Altre criticità ancora aperte</p>
            <p className="review-context-summary" style={{ margin: 0 }}>
              Sono ulteriori rilievi registrati dalla governance. Potrebbero richiedere
              un nuovo intervento se restano applicabili dopo la tua decisione.
            </p>
            <ul className="review-narrative-list">
              {openConcerns.map((code) => (
                <li key={code}>
                  <strong>{reasonCodeLabel(code)}</strong>
                  {reasonCodeExplanation(code) ? ` — ${reasonCodeExplanation(code)}` : null}
                </li>
              ))}
            </ul>
          </article>
        ) : null}

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
                <li className="flagged-value-item" key={item.value}>
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
                    <p>Se l'estratto non basta, apri la fonte e cerca «{item.value}» con Ctrl+F (anche con una notazione diversa). Leggi il paragrafo e controlla soggetto, soglia, unità e periodo. Puoi consultare anche le fonti selezionate elencate sotto.</p>
                    <p>Se confermi l'affermazione, indica nella nota fonte e passaggio verificati. Se resta incerta, non considerare il solo numero trovato una conferma.</p>
                  </div>
                </li>
              ))}
            </ul>
          </article>
        ) : null}

        <PriorDecisionsPanel
          decisions={humanReview?.decision_history ?? []}
          currentOperationDigest={pendingRequest?.operation_payload_digest ?? null}
        />

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
              <div className="review-source" key={reference.observation_id}>
                {reference.locator ? <SourceReference locator={reference.locator} /> : null}
                {/* The backend supplies a bounded excerpt, not the complete source. */}
                <details className="technical-details">
                  <summary>Leggi l'estratto disponibile (non il documento completo)</summary>
                  <div className="review-source-excerpt">{reference.content_excerpt}</div>
                  {reference.source_timestamp ? <p>Data registrata della fonte: {reference.source_timestamp}</p> : null}
                </details>
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
              <PillarDiagnosticCard
                key={diagnostic.pillar_name}
                diagnostic={diagnostic}
                decisionReasonCodes={reasonCodes}
              />
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
                { label: "Esito dell'agente", value: pendingRequest?.actor_result_type ?? null },
                { label: "Motivo del blocco dichiarato dall'agente", value: actorBlockingReason },
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
                { label: "Criticità aperte non selezionate", value: openConcerns.join(", ") || null },
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
