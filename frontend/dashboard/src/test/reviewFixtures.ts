import type { ReviewDetailResponse } from "../types";

// One fixture supplies both editor and proposal-renderer tests without live services.
// Build one modifiable request shared by the form-safety regression tests.
export function review(): ReviewDetailResponse {
  return { thread_id: "isolated", dashboard_snapshot: {
    runtime_state: "waiting_human_review", has_pending_review: true, is_terminal: false,
    reason_codes: ["capability_approval_required"],
    external_execution: {execution_intent: "execute_after_governance", retryable: false, attempt_count: 0, receipt_count: 0},
    human_review: {pending_request: {request_id: "request-1", allowed_decisions: ["approve_continue", "modify_and_continue", "ask_clarification", "abort"],
      external_communication_draft: {channel: "email", recipient: "review@example.test", subject: "Originale", body: "Originale", cc: ["cc@example.test"], metadata: {body_format: "plain"}},
    }},
  }};
}

