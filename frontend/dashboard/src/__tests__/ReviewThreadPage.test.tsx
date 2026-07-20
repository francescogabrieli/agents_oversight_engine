import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";

describe("ReviewThreadPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            thread_id: "thread-1",
            runtime_summary: {
              task_goal: "Inspect a governed draft.",
            },
            dashboard_snapshot: {
              runtime_state: "waiting_human_review",
              has_pending_review: true,
              is_terminal: false,
              reason_codes: ["human_review_required"],
              current_plan_step: {
                step_id: "step_1",
                objective: "Collect evidence.",
                rationale: "Need safe grounding.",
                expected_outcome: "Evidence gathered.",
                status: "in_progress",
              },
              governance: {
                directive: "wait_human_review",
              },
              oversight: {
                summary: "Evidence and risk warrant human review.",
              },
              external_communication: {
                phase: "human_review_required",
              },
              human_review: {
                pending_request: {
                  request_id: "thread-1:hitl",
                  allowed_decisions: ["approve_continue", "modify_and_continue"],
                  external_communication_draft: {
                    channel: "email",
                    body: "Draft body.",
                    recipient: "ops@example.com",
                    subject: "Draft subject",
                    metadata: { priority: "high" },
                  },
                },
              },
            },
          }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            thread_id: "thread-1",
            runtime_summary: {
              task_goal: "Inspect a governed draft.",
            },
            dashboard_snapshot: {
              runtime_state: "acting",
              has_pending_review: false,
              is_terminal: false,
              reason_codes: [],
              governance: {},
              oversight: {},
              external_communication: {},
            },
            operator_feedback: "The runtime resumed on the approved execution path.",
          }),
        })
        .mockResolvedValue({
          ok: true,
          json: async () => ({
            thread_id: "thread-1",
            runtime_summary: {
              task_goal: "Inspect a governed draft.",
            },
            dashboard_snapshot: {
              runtime_state: "acting",
              has_pending_review: false,
              is_terminal: false,
              reason_codes: [],
              governance: {},
              oversight: {},
              external_communication: {},
            },
          }),
        }),
    );
  });

  it("renders thread detail and submits a typed decision", async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter
        initialEntries={["/threads/thread-1"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <App />
      </MemoryRouter>,
    );

    expect((await screen.findAllByText("Collect evidence.")).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Submit decision" }));

    expect(fetch).toHaveBeenCalledWith(
      "/api/reviews/thread-1/resume",
      expect.objectContaining({
        method: "POST",
      }),
    );
  });
});
