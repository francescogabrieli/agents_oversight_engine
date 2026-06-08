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
          }),
        })
        .mockResolvedValue({
          ok: true,
          json: async () => ({ items: [] }),
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
});
