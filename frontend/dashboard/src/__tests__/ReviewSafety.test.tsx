import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DecisionForm } from "../components/DecisionForm";
import { ProposalPanel } from "../components/ProposalPanel";
import { SourceReference } from "../components/SourceReference";
import { DecisionDeliveryStatus } from "../components/DecisionDeliveryStatus";
import { externalExecutionNarrative } from "../components/executionPresentation";
import { pillarHighlights } from "../components/pillarSummary";
import type { ReviewDetailResponse } from "../types";

// One fixture supplies both editor and proposal-renderer tests without live services.
// Build one modifiable request shared by the form-safety regression tests.
function review(): ReviewDetailResponse {
  return { thread_id: "isolated", dashboard_snapshot: {
    runtime_state: "waiting_human_review", has_pending_review: true, is_terminal: false,
    reason_codes: ["capability_approval_required"],
    external_execution: {execution_intent: "execute_after_governance", retryable: false, attempt_count: 0, receipt_count: 0},
    human_review: {pending_request: {request_id: "request-1", allowed_decisions: ["approve_continue", "modify_and_continue", "ask_clarification", "abort"],
      external_communication_draft: {channel: "email", recipient: "review@example.test", subject: "Originale", body: "Originale", cc: ["cc@example.test"], metadata: {body_format: "plain"}},
    }},
  }};
}

describe("Safe review editing and presentation", () => {
  it("preserves edits on equivalent polling and clears them for a new request", async () => {
    const detail = review();
    const submit = vi.fn();
    const user = userEvent.setup();
    const {rerender} = render(<DecisionForm detail={detail} onSubmit={submit} />);
    expect(screen.getByRole("button", {name: "Invia decisione"})).toBeDisabled();
    await user.click(screen.getByRole("button", {name: "Modifica"}));
    await user.clear(screen.getByLabelText("Corpo modificato"));
    await user.type(screen.getByLabelText("Corpo modificato"), "Correzione persistente");
    await user.clear(screen.getByLabelText("Destinatario"));
    await user.type(screen.getByLabelText("Destinatario"), "updated@example.test");
    await user.clear(screen.getByLabelText("Oggetto"));
    await user.type(screen.getByLabelText("Oggetto"), "Oggetto aggiornato");
    rerender(<DecisionForm detail={JSON.parse(JSON.stringify(detail))} onSubmit={submit} />);
    expect(screen.getByLabelText("Corpo modificato")).toHaveValue("Correzione persistente");
    await user.click(screen.getByRole("button", {name: "Invia decisione: Modifica"}));
    expect(submit).toHaveBeenCalledWith(expect.objectContaining({external_communication_edit: expect.objectContaining({draft: expect.objectContaining({body: "Correzione persistente", recipient: "updated@example.test", subject: "Oggetto aggiornato", cc: ["cc@example.test"]})})}));
    detail.dashboard_snapshot!.human_review!.pending_request!.request_id = "request-2";
    rerender(<DecisionForm detail={detail} onSubmit={submit} />);
    expect(screen.getByRole("button", {name: "Invia decisione"})).toBeDisabled();
    expect(screen.queryByLabelText("Corpo modificato")).not.toBeInTheDocument();
  });

  it("preserves database edits but resets selection when the candidate changes", async () => {
    const detail = review();
    const request = detail.dashboard_snapshot!.human_review!.pending_request!;
    request.external_communication_draft = null;
    request.external_operation_proposal = {operation_family: "database", payload: {statement: "SELECT 1", profile_id: "read_only"}, payload_digest: "digest-1", modifiable: true};
    const user = userEvent.setup();
    const {rerender} = render(<DecisionForm detail={detail} onSubmit={vi.fn()} />);
    await user.click(screen.getByRole("button", {name: "Modifica"}));
    await user.clear(screen.getByLabelText("Dati modificati dell'operazione"));
    await user.type(screen.getByLabelText("Dati modificati dell'operazione"), "SELECT 2");
    rerender(<DecisionForm detail={structuredClone(detail)} onSubmit={vi.fn()} />);
    expect(screen.getByLabelText("Dati modificati dell'operazione")).toHaveValue("SELECT 2");
    request.external_operation_proposal.payload_digest = "digest-2";
    rerender(<DecisionForm detail={detail} onSubmit={vi.fn()} />);
    expect(screen.getByRole("button", {name: "Invia decisione"})).toBeDisabled();
  });

  it("requires clarification text and blocks stale submissions", async () => {
    const detail = review(); const user = userEvent.setup();
    const {rerender} = render(<DecisionForm detail={detail} onSubmit={vi.fn()} />);
    await user.click(screen.getByRole("button", {name: "Segnala chiarimento necessario"}));
    expect(screen.getByRole("button", {name: "Invia decisione: Segnala chiarimento necessario"})).toBeDisabled();
    await user.type(screen.getByLabelText(/Chiarimento richiesto/), "Escludi i risolti.");
    expect(screen.getByRole("button", {name: "Invia decisione: Segnala chiarimento necessario"})).toBeEnabled();
    rerender(<DecisionForm detail={detail} onSubmit={vi.fn()} disabled />);
    expect(screen.getByRole("button", {name: "Invia decisione: Segnala chiarimento necessario"})).toBeDisabled();
  });

  it("shows recipient and subject before any action and exposes SQL read-only", () => {
    const request = review().dashboard_snapshot!.human_review!.pending_request!;
    const {rerender} = render(<ProposalPanel request={request} />);
    expect(screen.getByText("review@example.test")).toBeInTheDocument();
    expect(screen.getByText("cc@example.test")).toBeInTheDocument();
    request.external_communication_draft = null;
    request.external_operation_proposal = {operation_family: "database", payload: {statement: "SELECT 1", profile_id: "read_only", parameters: {limit: 2}}, payload_digest: "digest", modifiable: false};
    rerender(<ProposalPanel request={request} />);
    expect(screen.getByText("SELECT 1")).toBeInTheDocument();
    expect(screen.getByText("read_only")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("does not turn non-web locators into executable links", () => {
    const {rerender} = render(<SourceReference locator="https://example.test/source" />);
    expect(screen.getByRole("link")).toHaveAttribute("rel", "noopener noreferrer");
    rerender(<SourceReference locator="javascript:alert(1)" />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("distinguishes uncertain execution, delivery and missing evidence facts", () => {
    expect(externalExecutionNarrative("execute_after_governance", "indeterminate")).toContain("potrebbe essere già stata eseguita");
    expect(externalExecutionNarrative("proposal_only", null)).not.toContain("approvata");
    const {rerender} = render(<DecisionDeliveryStatus status="pending" ownerAvailable={false} />);
    expect(screen.getByText(/Nessun processo runtime proprietario/)).toBeInTheDocument();
    rerender(<DecisionDeliveryStatus status="consumed" ownerAvailable />);
    expect(screen.getByText(/non attesta il completamento/)).toBeInTheDocument();
    expect(pillarHighlights("evidence", {selected_evidence_ref_count: 2})[0]).toMatchObject({tone: "neutral", value: "2, validità non riportata"});
  });
});
