const EXECUTION_INTENT_LABELS: Record<string, string> = {
  proposal_only: "Solo bozza, nessuna esecuzione reale",
  execute_after_governance: "Esecuzione prevista dopo i controlli",
};

const EXECUTION_STATUS_LABELS: Record<string, string> = {
  executed: "Eseguita",
  rejected_before_execution: "Bloccata prima dell'esecuzione",
  failed: "Fallita",
  indeterminate: "Esito non determinato",
};

const COMMUNICATION_PHASE_LABELS: Record<string, string> = {
  evidence_collection: "Raccolta evidenze",
  draft_in_progress: "Bozza in corso",
  draft_proposal: "Bozza proposta",
  human_review_required: "Revisione richiesta",
  approved_draft: "Bozza approvata",
  approved_for_execution: "Approvata per l'esecuzione",
  executed: "Eseguita",
  execution_failed: "Esecuzione fallita",
  indeterminate: "Esito non determinato",
  blocked: "Bloccata",
};

export function communicationPhaseLabel(phase: string | null | undefined) {
  if (!phase) return null;
  return COMMUNICATION_PHASE_LABELS[phase] ?? phase;
}

export function executionIntentLabel(intent: string | null | undefined) {
  if (!intent) return null;
  return EXECUTION_INTENT_LABELS[intent] ?? intent;
}

export function executionStatusLabel(status: string | null | undefined) {
  if (!status) return null;
  return EXECUTION_STATUS_LABELS[status] ?? status;
}

// One-sentence narrative of what the external operation card represents.
export function externalExecutionNarrative(
  intent: string | null | undefined,
  status: string | null | undefined,
) {
  if (intent === "proposal_only") {
    return "L'intento del task è produrre una proposta, non eseguire un'azione esterna.";
  }
  if (status === "executed") {
    return "L'operazione esterna è stata eseguita dopo l'approvazione della governance.";
  }
  if (status === "rejected_before_execution") {
    return "La governance ha bloccato l'operazione prima che venisse eseguita.";
  }
  if (status === "failed") {
    return "L'operazione esterna è stata tentata ma non è andata a buon fine.";
  }
  if (status === "indeterminate") {
    return "L'operazione potrebbe essere già stata eseguita, ma manca una conferma affidabile. Verifica l'esito presso il sistema destinatario; non ripetere l'azione per supposizione.";
  }
  return "L'operazione esterna non è ancora stata eseguita.";
}
