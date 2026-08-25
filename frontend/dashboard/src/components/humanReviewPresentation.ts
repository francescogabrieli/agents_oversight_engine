const DECISION_LABELS: Record<string, string> = {
  approve_continue: "Approve",
  approve_continue_read_only: "Approve read-only",
  modify_and_continue: "Modify",
  request_more_evidence: "More evidence",
  replan: "Replan",
  ask_clarification: "Clarify",
  abort: "Abort",
};

// Keep operator-facing decision labels consistent across review context and controls.
export function humanReviewDecisionLabel(decision: string) {
  return DECISION_LABELS[decision] ?? decision;
}
