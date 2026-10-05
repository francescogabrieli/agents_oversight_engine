import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { EvidenceComparisonPanel } from "../components/EvidenceComparisonPanel";
import { FlaggedEvidencePanel } from "../components/FlaggedEvidencePanel";
import { pillarHighlights } from "../components/pillarSummary";
import type { DashboardEvidenceReference } from "../types";

const sources: DashboardEvidenceReference[] = [
  { observation_id: "first", locator: "https://example.test/report", content_excerpt: "Titolo\n\nUna frase oltre il vecchio limite.", content_truncated: false, retrieval_kind: "fetched_document" },
  { observation_id: "second", locator: "controlled:report", content_excerpt: "Solo un estratto", content_truncated: true },
  { observation_id: "missing", content_excerpt: "", observation_available: false },
];

describe("Evidence comparison", () => {
  it("does not confuse retrieved sources with the selected evidence burden", () => {
    const rows = pillarHighlights("evidence", {
      distinct_independent_sources_count: 1, retrieved_independent_sources_count: 5,
      min_independent_sources_required: 3, independent_sources_satisfied: false,
    });
    expect(rows).toContainEqual({ label: "Fonti indipendenti citate", value: "1 su 3 richieste", tone: "warn" });
    expect(rows).toContainEqual({ label: "Fonti indipendenti recuperate complessivamente", value: "5 (anche non citate)", tone: "neutral" });
    expect(pillarHighlights("evidence", { retrieved_independent_sources_count: 5, min_independent_sources_required: 3 }).some((row) => row.value.includes("su 3"))).toBe(false);
  });

  it("does not turn a reported concern with omitted tokens into a clean result", () => {
    expect(pillarHighlights("evidence", { has_unanchored_literals_in_selection: true })).toContainEqual({ label: "Valori non riscontrati nelle fonti", value: "Presenti; valori non riportati", tone: "warn" });
  });

  it("compares the replan limit only to its episode and exposes exhausted exploration", () => {
    const rows = pillarHighlights("trajectory", { replan_count: 12, replan_episode_count: 1, max_replans_before_review: 3, replan_ceiling_reached: false, current_step_tool_attempt_count: 30, step_exploration_budget_exhausted: true });
    expect(rows).toContainEqual({ label: "Ripianificazioni complessive", value: "12", tone: "neutral" });
    expect(rows).toContainEqual({ label: "Ripianificazioni dall'ultima revisione", value: "1 su 3 consentite", tone: "ok" });
    expect(rows).toContainEqual({ label: "Tentativi di strumenti nel passo", value: "30 — limite raggiunto", tone: "warn" });
  });
  it("shows the recorded text immediately and retains source selection across polls", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<EvidenceComparisonPanel output="Affermazione dell'agente" references={sources} required />);
    expect(screen.getByText("Affermazione dell'agente")).toBeVisible();
    expect(screen.getByRole("region", { name: "Estratto della fonte selezionata" })).toHaveTextContent("Una frase oltre il vecchio limite.");
    await user.selectOptions(screen.getByRole("combobox"), "second");
    expect(screen.getByText(/Estratto parziale/)).toBeVisible();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    rerender(<EvidenceComparisonPanel output="Affermazione dell'agente" references={structuredClone(sources)} required />);
    expect(screen.getByRole("combobox")).toHaveValue("second");
    rerender(<EvidenceComparisonPanel output="Affermazione dell'agente" references={[sources[0]]} required />);
    expect(screen.getByRole("combobox")).toHaveValue("first");
    expect(screen.getByText(/Tutto il testo registrato/)).toBeVisible();
  });

  it("distinguishes unresolved references, empty observations and historical unknowns", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<EvidenceComparisonPanel references={sources} required />);
    await user.selectOptions(screen.getByRole("combobox"), "missing");
    expect(screen.getByText(/Riferimento selezionato non risolto/)).toBeVisible();
    rerender(<EvidenceComparisonPanel references={[{ observation_id: "empty", content_excerpt: "" }]} required />);
    expect(screen.getByText(/Estratto non disponibile/)).toBeVisible();
    expect(screen.getByText(/la completezza non è indicata/)).toBeVisible();
    rerender(<EvidenceComparisonPanel references={[]} required />);
    expect(screen.getByText(/Nessuna fonte selezionata disponibile/)).toBeVisible();
    expect(screen.getByText(/Testo prodotto non disponibile/)).toBeVisible();
  });

  it("shows legacy flagged values with missing context instead of dropping them", () => {
    render(<FlaggedEvidencePanel diagnostic={{ pillar_name: "evidence", required: true, reason_codes: [], facts: {
      unanchored_literal_excerpts: [null, {}, { value: "42%", excerpt: "La quota è 42%", source_matches: [null, { excerpt: "La quota registrata è 42%", locator: "https://example.test/report" }] }],
      unanchored_literals_in_selection: ["42%", "99%", 123],
    } }} />);
    expect(screen.getAllByRole("heading", { name: /Valore da verificare/ })).toHaveLength(2);
    const legacy = screen.getByRole("region", { name: "Testo dell'agente per 99%" });
    expect(within(legacy).getByText(/Contesto della frase non disponibile/)).toBeVisible();
    const passage = screen.getByRole("region", { name: "Passaggi da verificare per 42%" });
    expect(passage.querySelector("mark")).toHaveTextContent("42%");
    expect(screen.getByText(/Nessun passaggio di confronto disponibile/)).toBeVisible();
  });

  it("identifies the governed draft explicitly when a historical snapshot has no actor output", () => {
    render(<EvidenceComparisonPanel proposalBody="Bozza conservata nello snapshot" references={sources} required />);
    expect(screen.getByRole("heading", { name: "Testo della proposta in revisione" })).toBeVisible();
    expect(screen.getByText("Bozza conservata nello snapshot")).toBeVisible();
    expect(screen.queryByText(/Testo prodotto non disponibile/)).not.toBeInTheDocument();
  });
});
