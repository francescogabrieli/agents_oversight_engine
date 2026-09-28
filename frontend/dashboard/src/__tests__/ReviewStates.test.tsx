import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { DecisionForm } from "../components/DecisionForm";
import { DecisionDeliveryStatus } from "../components/DecisionDeliveryStatus";
import { review } from "../test/reviewFixtures";

describe("Review boundary states", () => {
  it.each([
    ["pending", "in attesa di essere presa in carico"],
    ["claimed", "elaborazione in corso"],
    ["consumed", "non attesta il completamento"],
    ["failed", "è fallita"],
  ])("distinguishes %s delivery from task completion", (status, message) => {
    render(<DecisionDeliveryStatus status={status} ownerAvailable={false} />);
    expect(screen.getByRole("status")).toHaveTextContent(message);
    expect(screen.getByText(/Nessun processo runtime proprietario/)).toBeVisible();
  });
  it("does not show a failed inbox load as an empty queue", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Offline")));
    render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><App /></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent("Offline");
    expect(screen.queryByText("Nessuna attività in attesa di revisione.")).not.toBeInTheDocument();
    expect(screen.getByText("Dati non disponibili")).toBeVisible();
  });

  it("explains a missing snapshot and handles literal percent signs in thread IDs", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ thread_id: "50%" }) }));
    render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/threads/50%25"]}><App /></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent("Lo snapshot non è presente");
    expect(fetch).toHaveBeenCalledWith("/api/reviews/50%25", expect.anything());
    expect(screen.queryByRole("button", { name: /Invia decisione/ })).not.toBeInTheDocument();
  });

  it("submits a clarification answer with its text and no authorization payload", async () => {
    const user = userEvent.setup();
    const detail = review();
    const submit = vi.fn().mockResolvedValue(undefined);
    render(<DecisionForm detail={detail} onSubmit={submit} />);
    await user.click(screen.getByRole("button", { name: "Rispondi al chiarimento" }));
    expect(screen.getByText(/Registra la tua risposta e riprende/)).toBeVisible();
    await user.type(screen.getByLabelText(/Risposta al chiarimento/), "Usa soltanto i dati pubblicati.");
    await user.click(screen.getByRole("button", { name: "Invia decisione: Rispondi al chiarimento" }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith(expect.objectContaining({ decision_type: "ask_clarification", clarification_text: "Usa soltanto i dati pubblicati." })));
    expect(submit.mock.calls[0][0]).not.toHaveProperty("external_operation_edit");
  });

  it("disables a selected decision removed by an updated request", async () => {
    const user = userEvent.setup();
    const detail = review();
    const submit = vi.fn();
    const { rerender } = render(<DecisionForm detail={detail} onSubmit={submit} />);
    await user.click(screen.getByRole("button", { name: "Approva" }));
    detail.dashboard_snapshot!.human_review!.pending_request!.allowed_decisions = ["abort"];
    rerender(<DecisionForm detail={detail} onSubmit={submit} />);
    expect(screen.getByRole("button", { name: "Invia decisione: Approva" })).toBeDisabled();
    expect(submit).not.toHaveBeenCalled();
  });

  it.each([
    ["judge_uncertain", "Sblocchi soltanto"],
    ["evidence_anchoring_requires_review", "Sblocchi soltanto"],
    ["capability_approval_required", "Autorizzi questa esatta proposta"],
    ["external_execution_reconciliation_required", "Non autorizzi un nuovo tentativo"],
  ])("explains approval scope for %s", async (reason, explanation) => {
    const detail = review();
    detail.dashboard_snapshot!.human_review!.pending_request!.reason_codes = [reason];
    const user = userEvent.setup();
    render(<DecisionForm detail={detail} onSubmit={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Approva" }));
    expect(screen.getByText(new RegExp(explanation))).toBeVisible();
  });

  it("keeps acknowledgement keyboard focus in the dialog and closes with Escape", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [], completed_items: [] }) }));
    const user = userEvent.setup();
    render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={[{ pathname: "/", state: { decisionFeedback: "Decisione registrata." } }]}><App /></MemoryRouter>);
    const close = screen.getByRole("button", { name: "Chiudi" });
    expect(close).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it.each(["completed", "aborted", "acting"])("keeps %s threads non-interactive and preserves decision history", async (state) => {
    const detail = review();
    const snapshot = detail.dashboard_snapshot!;
    snapshot.runtime_state = state;
    snapshot.is_terminal = state !== "acting";
    snapshot.has_pending_review = false;
    snapshot.human_review!.pending_request = null;
    snapshot.human_review!.decision_history = [{ request_id: "old", decision_type: "replan", note: "Cercare un'altra fonte" }];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => detail }));
    render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/threads/isolated"]}><App /></MemoryRouter>);
    expect(await screen.findByText(/Nessuna azione di revisione in sospeso/)).toBeVisible();
    expect(within(screen.getByRole("article", { name: "Decisioni già prese" })).getByText(/Cercare un'altra fonte/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /Invia decisione/ })).not.toBeInTheDocument();
  });

  it("shows the reconciliation receipt before the decision without hiding its reference", async () => {
    const detail = review();
    detail.dashboard_snapshot!.human_review!.pending_request!.reason_codes = ["external_execution_reconciliation_required"];
    Object.assign(detail.dashboard_snapshot!.external_execution, { status: "indeterminate", external_reference: "receipt-42", connector_id: "mail", attempt_count: 1 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => detail }));
    render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/threads/isolated"]}><App /></MemoryRouter>);
    const label = await screen.findByText("Esito da riconciliare");
    expect(label).toBeVisible();
    expect(screen.getAllByText("receipt-42").some((element) => !element.closest("details"))).toBe(true);
    const decision = screen.getByRole("region", { name: "La tua decisione" });
    expect(label.compareDocumentPosition(decision) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
