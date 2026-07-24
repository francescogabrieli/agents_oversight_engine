export type ReviewInboxEntry = {
  thread_id: string;
  runtime_state: string | null;
  updated_at: string | null;
  task_goal: string | null;
  review_summary: string | null;
  allowed_decisions: string[];
  request_id: string | null;
  external_communication_phase: string | null;
  execution_intent?: string | null;
  external_execution_status?: string | null;
};

export type DashboardPlanStep = {
  step_id: string;
  objective: string;
  rationale: string;
  expected_outcome: string;
  status: string;
};

export type DashboardPanel = Record<string, unknown>;

export type HumanReviewPendingRequest = {
  request_id: string;
  review_summary?: string | null;
  allowed_decisions: string[];
  external_communication_draft?: ExternalCommunicationDraft | null;
  operation_id?: string | null;
  operation_payload_digest?: string | null;
  operation_proposal_summary?: string | null;
  operation_target?: string | null;
  judge_findings?: string[];
  external_operation_proposal?: {
    operation_family: "communication" | "database" | "generic";
    payload: Record<string, unknown>;
    payload_digest: string;
    modifiable: boolean;
  } | null;
};

export type ExternalCommunicationDraft = {
  channel: string;
  recipient?: string | null;
  subject?: string | null;
  body: string;
  cc?: string[];
  bcc?: string[];
  metadata: Record<string, unknown>;
};

export type ExternalExecutionPanel = {
  execution_intent: "proposal_only" | "execute_after_governance";
  capability_id?: string | null;
  status?: "executed" | "rejected_before_execution" | "failed" | "indeterminate" | null;
  payload_digest?: string | null;
  connector_kind?: string | null;
  connector_id?: string | null;
  server_id?: string | null;
  tool_name?: string | null;
  external_reference?: string | null;
  summary?: string | null;
  error_code?: string | null;
  retryable: boolean;
  attempt_count: number;
  receipt_count: number;
};

export type DashboardSnapshot = {
  runtime_state: string;
  resume_state?: string | null;
  has_pending_review: boolean;
  is_terminal: boolean;
  reason_codes: string[];
  current_plan_step?: DashboardPlanStep | null;
  governance?: DashboardPanel | null;
  oversight?: DashboardPanel | null;
  behavioral?: DashboardPanel | null;
  external_communication?: DashboardPanel | null;
  external_execution: ExternalExecutionPanel;
  human_review?: {
    pending_request?: HumanReviewPendingRequest | null;
    allowed_decisions?: string[];
    review_summary?: string | null;
  } | null;
};

export type ReviewDetailResponse = {
  thread_id: string;
  runtime_summary?: Record<string, unknown> | null;
  dashboard_snapshot?: DashboardSnapshot | null;
  operator_feedback?: string;
};

export type HumanReviewDecisionPayload = {
  thread_id: string;
  request_id: string;
  decision_type: string;
  reviewer_id?: string;
  note?: string;
  clarification_text?: string;
  external_communication_edit?: {
    note?: string;
    draft: ExternalCommunicationDraft;
  };
  external_operation_edit?: {
    note?: string;
    payload: Record<string, unknown>;
  };
};
