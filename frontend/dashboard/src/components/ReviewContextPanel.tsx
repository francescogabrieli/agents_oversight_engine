import { useState } from "react";
import type { DashboardPillarDiagnostic, DashboardSnapshot } from "../types";
import { actorResultLabel, isPlaceholderBlockingReason } from "./actorResultPresentation";
import { humanReviewDecisionLabel } from "./humanReviewPresentation";
import { KeyValueGrid } from "./KeyValueGrid";
import { PillarDiagnosticCard } from "./PillarDiagnosticCard";
import { PriorDecisionsPanel } from "./PriorDecisionsPanel";
import { reasonCodeChipLabel, reasonCodeExplanation, reasonCodeLabel } from "./reasonCodePresentation";
import { runtimeStateLabel } from "./runtimeStatePresentation";
import { FlaggedEvidencePanel } from "./FlaggedEvidencePanel";
import { EvidenceComparisonPanel } from "./EvidenceComparisonPanel";

type ReviewContextPanelProps = {
  taskGoal: string | null;
  snapshot: DashboardSnapshot;
};

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
  const allowedDecisions =
    pendingRequest?.allowed_decisions ?? humanReview?.allowed_decisions ?? [];
  const currentStepObjective = snapshot.current_plan_step?.objective ?? null;
  const actorHeading = actorResultLabel(pendingRequest?.actor_result_type);
  const actorSummary = pendingRequest?.actor_summary ?? null;
  const actorBlockingReason = pendingRequest?.actor_blocking_reason ?? null;
  const hasActorVoice = Boolean(actorSummary || actorBlockingReason);
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
            Leggi il motivo della fermata, confronta i dati e scegli come proseguire.
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
            <p className="review-diagnostic-title">{actorHeading ?? "Messaggio dell’agente"}</p>
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

        <EvidenceComparisonPanel
          output={humanReview?.actor_output_text}
          proposalBody={pendingRequest?.external_communication_draft?.body}
          references={humanReview?.selected_evidence_references ?? []}
          required={humanReview?.evidence_diagnostic?.required === true}
        />
        <FlaggedEvidencePanel diagnostic={humanReview?.evidence_diagnostic} />

        <PriorDecisionsPanel
          decisions={humanReview?.decision_history ?? []}
          currentOperationDigest={pendingRequest?.operation_payload_digest ?? null}
        />

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
