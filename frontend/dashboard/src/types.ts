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
  decision_type?: string | null;
  delivery_status?: string | null;
  owner_available?: boolean;
  reason_codes?: string[];
};

export type ReviewInboxResponse = {
  items: ReviewInboxEntry[];
  completed_items: ReviewInboxEntry[];
};

export type DashboardPlanStep = {
  step_id: string;
  objective: string;
  rationale: string;
  expected_outcome: string;
  status: string;
};

export type DashboardPanel = Record<string, unknown>;

export type DashboardGovernancePanel = DashboardPanel & {
  directive?: string;
  summary?: string | null;
  selected_reason?: string | null;
  reason_codes?: string[];
  // Concerns OPA evaluated as also blocking but did not select as the primary reason.
  // They stay open after this decision, so the operator has to see them.
  rejected_alternatives?: Record<string, string> | null;
};

export type DashboardOversightPanel = DashboardPanel & {
  summary?: string | null;
};

export type DashboardPillarDiagnostic = {
  pillar_name: "evidence" | "trajectory";
  required: boolean;
  mode?: string | null;
  level?: string | null;
  summary?: string | null;
  reason_codes: string[];
  facts: Record<string, unknown>;
};

export type DashboardEvidenceReference = {
  observation_id: string;
  locator?: string | null;
  source_timestamp?: string | null;
  content_excerpt: string;
  content_truncated?: boolean | null;
  observation_available?: boolean;
  retrieval_kind?: string | null;
};

export type HumanReviewPendingRequest = {
  request_id: string;
  actor_result_type?: string | null;
  // What the actor itself said when the run stopped: without these, a clarification or
  // a blocked step reaches the operator as a bare reason code with no question in it.
  actor_summary?: string | null;
  actor_blocking_reason?: string | null;
  review_summary?: string | null;
  reason_codes?: string[];
  resume_state?: string | null;
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

export type HumanReviewDecisionRecord = {
  request_id: string;
  decision_type: string;
  note?: string | null;
  reviewer_id?: string | null;
  approved_operation_digest?: string | null;
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
  governance?: DashboardGovernancePanel | null;
  oversight?: DashboardOversightPanel | null;
  behavioral?: DashboardPanel | null;
  external_communication?: DashboardPanel | null;
  external_execution: ExternalExecutionPanel;
  human_review?: {
    pending_request?: HumanReviewPendingRequest | null;
    decision_history?: HumanReviewDecisionRecord[];
    allowed_decisions?: string[];
    review_summary?: string | null;
    resume_state?: string | null;
    judge_findings?: string[];
    selected_evidence_references?: DashboardEvidenceReference[];
    actor_output_text?: string | null;
    evidence_diagnostic?: DashboardPillarDiagnostic | null;
    trajectory_diagnostic?: DashboardPillarDiagnostic | null;
  } | null;
};

export type ReviewDetailResponse = {
  thread_id: string;
  runtime_summary?: Record<string, unknown> | null;
  dashboard_snapshot?: DashboardSnapshot | null;
  operator_feedback?: string;
  decision_submission?: {
    submission_id: string;
    thread_id: string;
    request_id: string;
    decision_type: string;
    reviewer_id?: string | null;
    submitted_at: string;
    delivery_status: string;
    owner_available: boolean;
    error?: string | null;
  };
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
