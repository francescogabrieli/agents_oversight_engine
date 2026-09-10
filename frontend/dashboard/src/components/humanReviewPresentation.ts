const DECISION_LABELS: Record<string, string> = {
  approve_continue: "Approva",
  approve_continue_read_only: "Approva in sola lettura",
  modify_and_continue: "Modifica",
  request_more_evidence: "Richiedi più evidenze",
  replan: "Ripianifica",
  ask_clarification: "Segnala chiarimento necessario",
  abort: "Interrompi",
};

const DECISION_EXPLANATIONS: Record<string, string> = {
  approve_continue: "L'attività prosegue così come proposta.",
  approve_continue_read_only:
    "Richiedi la prosecuzione nel vincolo di sola lettura. Non autorizzi una proposta mutante invariata; il runtime verifica la compatibilità.",
  modify_and_continue: "La proposta modificata torna alla governance: la modifica non equivale all'autorizzazione di invio o esecuzione.",
  request_more_evidence: "L'agente riprende la raccolta delle evidenze. Spiega nella nota quali informazioni mancano.",
  replan: "L'attività torna alla fase di pianificazione per scegliere un percorso diverso.",
  ask_clarification: "Registra quale chiarimento serve e mantiene il caso sospeso. Non risponde all'agente e non autorizza operazioni esterne.",
  abort: "L'attività viene interrotta definitivamente.",
};

// Keep operator-facing decision labels consistent across review context and controls.
export function humanReviewDecisionLabel(decision: string) {
  return DECISION_LABELS[decision] ?? decision;
}

// Short plain-language explanation of what choosing a given decision does.
export function humanReviewDecisionExplanation(decision: string) {
  return DECISION_EXPLANATIONS[decision] ?? null;
}
