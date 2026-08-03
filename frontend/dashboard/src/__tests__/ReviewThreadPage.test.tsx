import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { DecisionForm } from "../components/DecisionForm";
import type { ReviewDetailResponse } from "../types";

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
                  allowed_decisions: [
                    "approve_continue",
                    "approve_continue_read_only",
                    "modify_and_continue",
                    "request_more_evidence",
                    "replan",
                    "ask_clarification",
                    "abort",
                  ],
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
            decision_submission: {
              submission_id: "submission-1",
              thread_id: "thread-1",
              request_id: "thread-1:hitl",
              decision_type: "approve_continue",
              submitted_at: "2026-08-03T10:00:00+00:00",
              delivery_status: "pending",
              owner_available: true,
            },
          }),
        })
        .mockResolvedValue({
          ok: true,
          json: async () => ({
            items: [],
            completed_items: [],
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
    for (const label of [
      "Approve",
      "Approve read-only",
      "Modify",
      "More evidence",
      "Replan",
      "Clarify",
      "Abort",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    await user.click(screen.getByRole("button", { name: "Submit decision" }));

    expect(fetch).toHaveBeenCalledWith(
      "/api/reviews/thread-1/resume",
      expect.objectContaining({
        method: "POST",
      }),
    );
    expect(
      await screen.findByRole("dialog", { name: "Decisione inviata correttamente" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Submit decision" })).not.toBeInTheDocument();
  });

  it("keeps the selected decision when polling returns an equivalent review payload", async () => {
    const user = userEvent.setup();
    const detail: ReviewDetailResponse = {
      thread_id: "thread-1",
      dashboard_snapshot: {
        runtime_state: "waiting_human_review",
        has_pending_review: true,
        is_terminal: false,
        reason_codes: ["human_review_required"],
        external_execution: {
          execution_intent: "proposal_only",
          retryable: false,
          attempt_count: 0,
          receipt_count: 0,
        },
        human_review: {
          pending_request: {
            request_id: "thread-1:hitl",
            allowed_decisions: ["approve_continue", "replan"],
          },
        },
      },
    };
    const { rerender } = render(
      <DecisionForm detail={detail} onSubmit={vi.fn().mockResolvedValue(undefined)} />,
    );

    await screen.findByRole("button", { name: "Replan" });
    await user.click(screen.getByRole("button", { name: "Replan" }));
    expect(screen.getByRole("button", { name: "Replan" })).toHaveClass("active");

    const pendingRequest = detail.dashboard_snapshot?.human_review?.pending_request;
    rerender(
      <DecisionForm
        detail={{
          ...detail,
          dashboard_snapshot: {
            ...detail.dashboard_snapshot!,
            human_review: {
              pending_request: {
                ...pendingRequest!,
                allowed_decisions: [...pendingRequest!.allowed_decisions],
              },
            },
          },
        }}
        onSubmit={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByRole("button", { name: "Replan" })).toHaveClass("active");
  });
});
