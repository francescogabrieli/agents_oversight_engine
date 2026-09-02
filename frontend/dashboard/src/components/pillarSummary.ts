// Pillar facts are audit vocabulary (see the *_REVIEW_FACTS tuples in
// layer2_runtime_governance/hitl/dashboard.py), not operator prose: an evidence stop
// ships ~38 booleans and counters, and the two or three that actually decide the
// review disappear into the table. This module is a display-only projection - it never
// re-derives a verdict, it only states in Italian what the pillar already observed.

export type PillarHighlight = {
  label: string;
  value: string;
  tone: "ok" | "warn" | "neutral";
};

type Facts = Record<string, unknown>;

function num(facts: Facts, key: string): number | null {
  const value = facts[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function flag(facts: Facts, key: string): boolean | null {
  const value = facts[key];
  return typeof value === "boolean" ? value : null;
}

function tokens(facts: Facts, key: string): string[] {
  const value = facts[key];
  if (!Array.isArray(value)) return [];
  return value.filter((entry): entry is string => typeof entry === "string");
}

function yesNo(value: boolean): string {
  return value ? "Sì" : "No";
}

// Only emit a row whose underlying fact was actually reported, so an inactive pillar
// stays visibly empty instead of showing fabricated zeros.
function push(
  rows: PillarHighlight[],
  label: string,
  value: string | null,
  tone: PillarHighlight["tone"],
) {
  if (value === null) return;
  rows.push({ label, value, tone });
}

function evidenceHighlights(facts: Facts): PillarHighlight[] {
  const rows: PillarHighlight[] = [];

  const retrieved = num(facts, "retrieved_independent_sources_count");
  const required = num(facts, "min_independent_sources_required");
  const sourcesOk = flag(facts, "independent_sources_satisfied");
  if (retrieved !== null && required !== null) {
    push(
      rows,
      "Fonti indipendenti",
      `${retrieved} su ${required} richieste`,
      sourcesOk === false ? "warn" : "ok",
    );
  }

  const refCount = num(facts, "selected_evidence_ref_count");
  const refsValid = flag(facts, "selected_evidence_refs_valid");
  if (refCount !== null) {
    push(
      rows,
      "Prove citate dall'agente",
      refsValid === false ? `${refCount}, ma non tutte valide` : `${refCount}, tutte valide`,
      refsValid === false ? "warn" : "ok",
    );
  }

  const contentOk = flag(facts, "retrieved_content_satisfied");
  if (contentOk !== null) {
    push(
      rows,
      "Contenuto davvero recuperato",
      contentOk ? "Sì, testo integrale" : "No, solo titoli o snippet",
      contentOk ? "ok" : "warn",
    );
  }

  const literals = tokens(facts, "unanchored_literals_in_selection");
  const universeLiterals = tokens(facts, "unanchored_literals_in_universe");
  const flagged = literals.length > 0 ? literals : universeLiterals;
  if (flagged.length > 0 || flag(facts, "has_unanchored_literals_in_selection") !== null) {
    push(
      rows,
      "Valori non riscontrati nelle fonti",
      flagged.length > 0 ? `${flagged.length} (${flagged.join(", ")})` : "nessuno",
      flagged.length > 0 ? "warn" : "ok",
    );
  }

  const locators = tokens(facts, "unanchored_locators_in_selection");
  if (locators.length > 0) {
    push(rows, "Link citati mai recuperati", `${locators.length}`, "warn");
  }

  // The count is the loop signal: it says how many times this same stop already
  // happened, which is invisible in a single-request view.
  const requestCount = num(facts, "evidence_request_count");
  const ceiling = flag(facts, "evidence_request_ceiling_reached");
  if (requestCount !== null) {
    push(
      rows,
      "Richieste di più prove già fatte",
      ceiling ? `${requestCount} — limite raggiunto` : `${requestCount}`,
      ceiling || requestCount > 0 ? "warn" : "neutral",
    );
  }

  if (flag(facts, "circular_reference_detected")) {
    push(rows, "Autocitazione rilevata", "Sì", "warn");
  }

  const burden = flag(facts, "burden_satisfied");
  if (burden !== null) {
    push(
      rows,
      "Onere di prova del passo",
      burden ? "soddisfatto" : "non soddisfatto",
      burden ? "ok" : "warn",
    );
  }

  if (flag(facts, "insufficiency_declared_in_payload")) {
    push(rows, "L'agente dichiara prove insufficienti", "Sì", "neutral");
  }

  const adjudicated = flag(facts, "anchoring_concern_adjudicated");
  if (adjudicated !== null) {
    push(
      rows,
      "Segnalazione già decisa da una persona",
      yesNo(adjudicated),
      adjudicated ? "ok" : "neutral",
    );
  }

  return rows;
}

function trajectoryHighlights(facts: Facts): PillarHighlight[] {
  const rows: PillarHighlight[] = [];

  const replans = num(facts, "replan_count");
  const maxReplans = num(facts, "max_replans_before_review");
  const replanCeiling = flag(facts, "replan_ceiling_reached");
  if (replans !== null) {
    push(
      rows,
      "Ripianificazioni",
      maxReplans !== null ? `${replans} su ${maxReplans} consentite` : `${replans}`,
      replanCeiling ? "warn" : "ok",
    );
  }

  const calls = num(facts, "tool_call_count");
  const failed = num(facts, "failed_tool_call_count");
  if (calls !== null) {
    push(
      rows,
      "Chiamate a strumenti",
      failed !== null ? `${calls}, di cui ${failed} fallite` : `${calls}`,
      failed !== null && failed > 0 ? "warn" : "ok",
    );
  }

  const consecutive = num(facts, "consecutive_tool_failures");
  if (consecutive !== null && consecutive > 0) {
    push(rows, "Errori consecutivi", `${consecutive}`, "warn");
  }

  if (flag(facts, "unchanged_resubmission")) {
    push(rows, "Proposta ripresentata identica", "Sì", "warn");
  }
  if (flag(facts, "repeated_rejected_payload_digest")) {
    push(rows, "Proposta già respinta e riproposta", "Sì", "warn");
  }

  const churn = num(facts, "action_churn_count");
  if (churn !== null && churn > 0) {
    push(rows, "Oscillazioni sulla stessa azione", `${churn}`, "warn");
  }

  const revisions = num(facts, "external_action_revision_count");
  if (revisions !== null && revisions > 0) {
    push(
      rows,
      "Correzioni sulla proposta esterna",
      flag(facts, "action_revision_limit_reached") ? `${revisions} — limite raggiunto` : `${revisions}`,
      "warn",
    );
  }

  return rows;
}

// Project one pillar's facts into the short list an operator can read at a glance.
export function pillarHighlights(pillarName: string, facts: Facts): PillarHighlight[] {
  if (pillarName === "evidence") return evidenceHighlights(facts);
  if (pillarName === "trajectory") return trajectoryHighlights(facts);
  return [];
}
