// Typed governance reason codes are audit vocabulary (see GovernanceReasonCode in
// governance_components/contracts.py), not operator-facing text: OPA's own
// selected_reason/summary strings are English sentences meant for the trace and the
// thesis evaluation, not for the person clicking through a live review. This module is
// a display-only translation layer - it never changes which code was selected, only
// how the same code reads to a human.

const REASON_CODE_LABELS: Record<string, string> = {
  policy_version_mismatch: "Versione della policy non allineata",
  static_policy_hard_deny: "Bloccato da una regola fissa",
  judge_required_unavailable: "Verifica automatica non disponibile",
  required_pillar_unavailable: "Controllo obbligatorio non disponibile",
  ask_clarification_required: "Serve un chiarimento",
  replan_ceiling_reached: "Troppi tentativi di ripianificazione",
  consecutive_tool_failures: "Troppi errori consecutivi",
  step_exploration_budget_exhausted: "Passo bloccato troppo a lungo",
  action_churn: "L'azione proposta oscilla senza convergere",
  replan_request_stalled: "Richiesta di ripianificazione non valida",
  unresolvable_revision: "Correzione richiesta non risolvibile",
  rejected_candidate_resubmitted: "Ripresentata una proposta già respinta",
  unchanged_candidate_resubmission: "Ripresentata la stessa identica proposta",
  actor_requested_replan: "Ripianificazione richiesta dall'attività",
  judge_unsafe: "La verifica automatica ha giudicato l'azione non sicura",
  judge_uncertain: "La verifica automatica non è riuscita a garantire la sicurezza",
  action_revision_limit_reached: "Troppe correzioni sulla stessa proposta",
  judge_needs_revision: "La verifica automatica chiede una correzione",
  evidence_request_limit_reached: "Prove insufficienti dopo ripetuti tentativi",
  evidence_anchoring_requires_review: "Valori nel testo non riscontrati nelle fonti",
  invalid_or_missing_selected_evidence: "Prove citate non valide",
  external_low_actor_self_confidence: "L'attività stessa non è sicura del risultato",
  capability_approval_required: "Serve la tua autorizzazione esplicita",
  external_execution_reconciliation_required: "Esito dell'invio da verificare",
};

const REASON_CODE_EXPLANATIONS: Record<string, string> = {
  policy_version_mismatch:
    "Il sistema di controllo è cambiato durante l'esecuzione: l'attività si è fermata per sicurezza.",
  static_policy_hard_deny: "Una regola fissa e non aggirabile ha bloccato l'attività.",
  judge_required_unavailable:
    "La verifica automatica di sicurezza non ha risposto correttamente: l'attività si ferma finché non torna disponibile.",
  required_pillar_unavailable:
    "Un controllo obbligatorio non ha potuto essere eseguito: l'attività si ferma per sicurezza.",
  ask_clarification_required: "L'attività ha bisogno di un'indicazione scritta da parte tua per continuare.",
  replan_ceiling_reached:
    "L'attività ha cambiato strategia troppe volte senza arrivare a una soluzione: decidi tu come proseguire.",
  consecutive_tool_failures:
    "Diversi tentativi di reperire informazioni sono falliti di seguito: probabilmente serve un intervento.",
  step_exploration_budget_exhausted:
    "Il passo attuale ha richiesto troppi tentativi senza concludersi: probabilmente serve un intervento.",
  action_churn: "L'attività continua a proporre varianti della stessa azione senza convergere su una versione definitiva.",
  replan_request_stalled:
    "L'attività ha chiesto di ripianificare, ma senza una ragione nuova valida: la richiesta non è stata concessa.",
  unresolvable_revision:
    "È stata chiesta una correzione che l'attività non ha le informazioni per soddisfare.",
  rejected_candidate_resubmitted: "L'attività ha riproposto una versione già scartata in precedenza.",
  unchanged_candidate_resubmission: "L'attività ha riproposto la stessa identica versione senza modifiche.",
  actor_requested_replan: "L'attività ha chiesto di cambiare strategia con una ragione nuova valida.",
  judge_unsafe: "La verifica automatica ha rilevato un problema di sicurezza nell'azione proposta.",
  judge_uncertain:
    "La verifica automatica non è riuscita a stabilire con sicurezza se l'azione proposta va bene.",
  action_revision_limit_reached:
    "La stessa proposta è stata corretta più volte senza risolvere i problemi rilevati.",
  judge_needs_revision: "La verifica automatica ha chiesto una correzione all'azione proposta.",
  evidence_request_limit_reached:
    "L'attività ha provato più volte a raccogliere prove sufficienti, senza riuscirci.",
  evidence_anchoring_requires_review:
    "Alcuni valori nel testo (numeri, date, importi) non corrispondono esattamente a quanto trovato nelle fonti consultate. Potrebbero essere corretti ma espressi in un altro modo: va verificato da una persona.",
  invalid_or_missing_selected_evidence:
    "L'attività cita come prova qualcosa che non esiste o non è più valido.",
  external_low_actor_self_confidence: "L'attività stessa segnala di non essere sicura del risultato proposto.",
  capability_approval_required:
    "Questa azione richiede sempre un'autorizzazione esplicita prima di essere eseguita.",
  external_execution_reconciliation_required:
    "Un'azione esterna è stata avviata ma il suo esito non è confermato: va verificato manualmente prima di procedere.",
};

// Codes that describe supporting detail (which structural family failed, etc.) rather
// than the primary stop reason. Kept short since they render as compact chips.
const REASON_CODE_DETAIL_LABELS: Record<string, string> = {
  invalid_evidence_ref: "riferimento inesistente",
  missing_evidence_ref: "riferimento mancante",
  duplicate_evidence_ref: "riferimento duplicato",
  empty_observation_content: "osservazione senza contenuto",
  unresolvable_provenance: "provenienza non risolvibile",
  unknown_step_lineage: "passo non riconosciuto",
  candidate_digest_mismatch: "impronta non corrispondente",
  missing_evidence_burden: "onere di prova non dichiarato",
  insufficient_independent_sources: "fonti indipendenti insufficienti",
  retrieved_content_missing: "contenuto non davvero recuperato",
  title_only_evidence_fragility: "selezione fatta di soli titoli",
  circular_evidence_reference: "autocitazione",
  unanchored_locators_in_selection: "link citato mai recuperato",
  unanchored_locators_in_universe: "link citato mai recuperato",
  unanchored_literals_in_selection: "valore non riscontrato nelle fonti",
  unanchored_literals_in_universe: "valore non riscontrato nelle fonti",
};

// Primary, prominent label for the reason a run is paused (short heading).
export function reasonCodeLabel(code: string): string {
  return REASON_CODE_LABELS[code] ?? REASON_CODE_DETAIL_LABELS[code] ?? code;
}

// One clear, plain-language sentence explaining the primary reason. Returns null when
// no Italian mapping exists yet, so callers can fall back to the raw governance text
// instead of showing nothing.
export function reasonCodeExplanation(code: string): string | null {
  return REASON_CODE_EXPLANATIONS[code] ?? null;
}

// Compact label for a secondary/detail chip; falls back to the raw code so an
// unmapped code never disappears from the audit trail.
export function reasonCodeChipLabel(code: string): string {
  return REASON_CODE_DETAIL_LABELS[code] ?? REASON_CODE_LABELS[code] ?? code;
}
