import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DecisionForm } from "../components/DecisionForm";
import { ProposalPanel } from "../components/ProposalPanel";
import { SourceReference } from "../components/SourceReference";
import { DecisionDeliveryStatus } from "../components/DecisionDeliveryStatus";
import { externalExecutionNarrative } from "../components/executionPresentation";
import { pillarHighlights } from "../components/pillarSummary";
import { review } from "../test/reviewFixtures";

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
    await user.click(screen.getByRole("button", {name: "Rispondi al chiarimento"}));
    expect(screen.getByRole("button", {name: "Invia decisione: Rispondi al chiarimento"})).toBeDisabled();
    await user.type(screen.getByLabelText(/Risposta al chiarimento/), "Escludi i risolti.");
    expect(screen.getByRole("button", {name: "Invia decisione: Rispondi al chiarimento"})).toBeEnabled();
    rerender(<DecisionForm detail={detail} onSubmit={vi.fn()} disabled />);
    expect(screen.getByRole("button", {name: "Invia decisione: Rispondi al chiarimento"})).toBeDisabled();
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

  it("presents and edits a generic typed proposal without dropping its structured fields", async () => {
    const detail = review();
    const request = detail.dashboard_snapshot!.human_review!.pending_request!;
    request.external_communication_draft = null;
    request.external_operation_proposal = {operation_family: "generic", payload: {payload_type: "generic", target: "queue", arguments: {mode: "dry_run"}}, payload_digest: "generic-digest", modifiable: true};
    const user = userEvent.setup();
    const submit = vi.fn().mockResolvedValue(undefined);
    render(<><ProposalPanel request={request} /><DecisionForm detail={detail} onSubmit={submit} /></>);
    expect(screen.getByText("queue")).toBeVisible();
    await user.click(screen.getByRole("button", {name: "Modifica"}));
    await user.clear(screen.getByLabelText("Dati modificati dell'operazione"));
    await user.paste(JSON.stringify({target: "queue", arguments: {mode: "reviewed"}}));
    await user.click(screen.getByRole("button", {name: "Invia decisione: Modifica"}));
    expect(submit).toHaveBeenCalledWith(expect.objectContaining({external_operation_edit: expect.objectContaining({payload: {payload_type: "generic", target: "queue", arguments: {mode: "reviewed"}}})}));
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
