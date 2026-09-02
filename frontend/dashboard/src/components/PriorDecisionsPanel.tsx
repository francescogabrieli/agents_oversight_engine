import type { HumanReviewDecisionRecord } from "../types";
import { humanReviewDecisionLabel } from "./humanReviewPresentation";

type PriorDecisionsPanelProps = {
  decisions: HumanReviewDecisionRecord[];
  // Digest of the operation currently under review, used to recognize that the very
  // same payload has already been decided on before.
  currentOperationDigest?: string | null;
};

// A review request is presented as if it were the first one, so an operator answering
// the fifth identical stop cannot see that the previous four answers changed nothing.
// This panel makes the loop visible before another decision is taken.
export function PriorDecisionsPanel({
  decisions,
  currentOperationDigest,
}: PriorDecisionsPanelProps) {
  if (decisions.length === 0) return null;

  const repeatedOnSamePayload = currentOperationDigest
    ? decisions.filter((decision) => decision.approved_operation_digest === currentOperationDigest)
    : [];

  return (
    <article className="prior-decisions" aria-label="Decisioni già prese">
      <p className="review-diagnostic-title">
        Decisioni già prese su questa attività — {decisions.length}
      </p>
      {repeatedOnSamePayload.length > 0 ? (
        <p className="prior-decisions-warning">
          Hai già deciso {repeatedOnSamePayload.length}{" "}
          {repeatedOnSamePayload.length === 1 ? "volta" : "volte"} su questa identica proposta e
          l'attività si è fermata di nuovo: approvare ancora probabilmente non la sbloccherà.
        </p>
      ) : null}
      <ol className="prior-decisions-list">
        {decisions.map((decision, index) => (
          <li key={`${decision.request_id}-${index}`}>
            <span className="prior-decisions-index">{index + 1}.</span>{" "}
            <strong>{humanReviewDecisionLabel(decision.decision_type)}</strong>
            {decision.note ? <span className="prior-decisions-note"> — {decision.note}</span> : null}
            <span className="review-evidence-locator">{decision.request_id}</span>
          </li>
        ))}
      </ol>
    </article>
  );
}
