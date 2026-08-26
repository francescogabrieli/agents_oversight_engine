const DECISION_LABELS: Record<string, string> = {
  approve_continue: "Approva",
  approve_continue_read_only: "Approva in sola lettura",
  modify_and_continue: "Modifica",
  request_more_evidence: "Richiedi più evidenze",
  replan: "Ripianifica",
  ask_clarification: "Chiedi un chiarimento",
  abort: "Interrompi",
};

const DECISION_EXPLANATIONS: Record<string, string> = {
  approve_continue: "L'attività prosegue così come proposta.",
  approve_continue_read_only:
    "L'attività prosegue, ma senza eseguire l'azione esterna: nessun effetto reale viene prodotto.",
  modify_and_continue: "Puoi correggere il testo o i dati proposti prima che l'attività prosegua.",
  request_more_evidence: "L'attività si ferma finché non vengono raccolte prove aggiuntive.",
  replan: "L'attività torna alla fase di pianificazione per scegliere un percorso diverso.",
  ask_clarification: "L'attività si ferma in attesa di una tua indicazione scritta.",
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
