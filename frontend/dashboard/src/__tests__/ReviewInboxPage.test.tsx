import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";

describe("ReviewInboxPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            items: [
              {
                thread_id: "thread-1",
                runtime_state: "waiting_human_review",
                updated_at: "2026-06-05T10:00:00+00:00",
                task_goal: "Inspect a governed draft.",
                review_summary: "Review the external communication draft.",
                allowed_decisions: ["approve_continue", "modify_and_continue"],
                request_id: "thread-1:hitl",
                external_communication_phase: "human_review_required",
              },
            ],
            completed_items: [],
          }),
        })
        .mockResolvedValue({
          ok: true,
          json: async () => ({ items: [], completed_items: [] }),
        }),
    );
  });

  it("renders pending reviews and polls again after two seconds", async () => {
    render(
      <MemoryRouter
        initialEntries={["/"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Inspect a governed draft.")).toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2), { timeout: 3500 });
  });

  it("renders recently completed reviews as non-interactive", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          items: [],
          completed_items: [
            {
              thread_id: "thread-completed",
              runtime_state: "review_completed",
              updated_at: "2026-08-03T10:00:00+00:00",
              task_goal: "Resume the governed task.",
              review_summary: "The operator approved the exact candidate.",
              allowed_decisions: [],
              request_id: "thread-completed:hitl",
              external_communication_phase: null,
              decision_type: "approve_continue",
              delivery_status: "pending",
            },
          ],
        }),
      }),
    );

    render(
      <MemoryRouter
        initialEntries={["/"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Decisioni inviate")).toBeInTheDocument();
    expect(screen.getByText(/Decisione registrata, in attesa/)).toBeInTheDocument();
    expect(screen.getByText("Resume the governed task.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Resume the governed task/i })).not.toBeInTheDocument();
  });

  // The inbox row is the first thing an operator sees; it must not lead with OPA's
  // raw English audit sentence when the primary reason code has an Italian mapping.
  it("shows a plain-language Italian summary instead of the raw governance sentence", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          items: [
            {
              thread_id: "thread-anchoring",
              runtime_state: "waiting_human_review",
              updated_at: "2026-08-28T10:00:00+00:00",
              task_goal: "Prepare a sourced briefing.",
              review_summary:
                "Literal claims in the completion are absent from every observation and cannot be settled by further retrieval.",
              reason_codes: ["evidence_anchoring_requires_review"],
              allowed_decisions: ["approve_continue", "modify_and_continue"],
              request_id: "thread-anchoring:hitl",
              external_communication_phase: null,
            },
          ],
          completed_items: [],
        }),
      }),
    );

    render(
      <MemoryRouter
        initialEntries={["/"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <App />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText(/non corrispondono esattamente a quanto trovato nelle fonti/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/Literal claims in the completion are absent/),
    ).not.toBeInTheDocument();
  });
});
