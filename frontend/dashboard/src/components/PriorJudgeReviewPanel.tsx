import type { DashboardPriorJudgeReview } from "../types";

const verdictLabels: Record<string, string> = {
  needs_revision: "richiedeva una revisione",
  unsafe: "ha rilevato un rischio",
  uncertain: "non ha potuto confermare la proposta",
};

// Explain a prior Judge rejection and whether it used the same evidence selection.
export function PriorJudgeReviewPanel({ review }: { review: DashboardPriorJudgeReview | null | undefined }) {
  if (!review) return null;
  const evidenceLabel = {
    same: "La verifica precedente ha usato la stessa selezione di evidenze.",
    different: "La verifica precedente ha usato evidenze diverse; i rilievi si riferiscono a quella selezione.",
    unknown: "Non è possibile confrontare le evidenze usate nelle due verifiche.",
  }[review.evidence_selection_match];
  return (
    <article className="prior-judge-review" aria-label="Valutazione precedente del giudice automatico">
      <p className="review-diagnostic-title">La stessa proposta era già stata valutata</p>
      <p>Il giudice automatico {verdictLabels[review.verdict] ?? "aveva espresso un rilievo"}.</p>
      {review.summary ? <p>{review.summary}</p> : null}
      {review.findings.length > 0 ? (
        <ul className="review-narrative-list">
          {review.findings.map((finding, index) => <li key={`${finding}-${index}`}>{finding}</li>)}
        </ul>
      ) : null}
      <p className="tool-history-reason">{evidenceLabel}</p>
    </article>
  );
}
