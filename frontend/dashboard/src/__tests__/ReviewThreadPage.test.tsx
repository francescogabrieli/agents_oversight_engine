import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { DecisionForm } from "../components/DecisionForm";
import { ReviewContextPanel } from "../components/ReviewContextPanel";
import type { DashboardSnapshot, ReviewDetailResponse } from "../types";

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
                summary: "The total number of replan requests reached the configured ceiling.",
                selected_reason: "Replan ceiling reached; operator direction is required.",
              },
              oversight: {
                summary: "Evidence and risk warrant human review.",
              },
              external_communication: {
                phase: "human_review_required",
              },
              human_review: {
                judge_findings: ["The recipient scope is not supported by the draft evidence."],
                selected_evidence_references: [
                  {
                    observation_id: "obs-1",
                    locator: "https://example.test/source",
                    source_timestamp: "2026-08-01T10:00:00+00:00",
                    content_excerpt: "The source supports the reviewed claim.",
                  },
                ],
                pending_request: {
                  request_id: "thread-1:hitl",
                  review_summary: "Review the stalled trajectory before continuing.",
                  reason_codes: ["replan_ceiling_reached"],
                  resume_state: "ready_tool_execution",
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
                evidence_diagnostic: {
                  pillar_name: "evidence",
                  required: false,
                  mode: "inactive",
                  level: "unscored",
                  summary: "Evidence pillar inactive (not required).",
                  reason_codes: [],
                  facts: {},
                },
                trajectory_diagnostic: {
                  pillar_name: "trajectory",
                  required: true,
                  mode: "active",
                  level: "degraded",
                  summary: "The replan ceiling requires an operator decision.",
                  reason_codes: ["replan_ceiling_reached"],
                  facts: {
                    replan_count: 3,
                    max_replans_before_review: 3,
                    consecutive_tool_failures: 3,
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

    expect(
      await screen.findByRole("heading", { name: "Perché serve una decisione dell'operatore" }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText("Replan ceiling reached; operator direction is required.").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("Cosa ha notato la verifica automatica")).toBeInTheDocument();
    expect(screen.getByText("The recipient scope is not supported by the draft evidence.")).toBeInTheDocument();
    expect(screen.getByText("Fonti alla base della proposta")).toBeInTheDocument();
    expect(screen.getByText("The source supports the reviewed claim.")).toBeInTheDocument();
    expect(screen.getByText("Testo prodotto")).toBeInTheDocument();
    expect((await screen.findAllByText("Collect evidence.")).length).toBeGreaterThan(0);

    await user.click(screen.getByText("Mostra i dati osservati"));
    expect(screen.getByText("Replan Count")).toBeInTheDocument();
    expect(screen.getByText("Consecutive Tool Failures")).toBeInTheDocument();

    for (const label of [
      "Approva",
      "Approva in sola lettura",
      "Modifica",
      "Richiedi più evidenze",
      "Ripianifica",
      "Chiedi un chiarimento",
      "Interrompi",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    await user.click(screen.getByRole("button", { name: "Invia decisione" }));

    expect(fetch).toHaveBeenCalledWith(
      "/api/reviews/thread-1/resume",
      expect.objectContaining({
        method: "POST",
      }),
    );
    expect(
      await screen.findByRole("dialog", { name: "Decisione inviata correttamente" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Invia decisione" })).not.toBeInTheDocument();
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

    await screen.findByRole("button", { name: "Ripianifica" });
    await user.click(screen.getByRole("button", { name: "Ripianifica" }));
    expect(screen.getByRole("button", { name: "Ripianifica" })).toHaveClass("active");

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

    expect(screen.getByRole("button", { name: "Ripianifica" })).toHaveClass("active");
  });

  it("renders actionable context for a non-external trajectory review", () => {
    const snapshot: DashboardSnapshot = {
      runtime_state: "waiting_human_review",
      resume_state: "ready_tool_execution",
      has_pending_review: true,
      is_terminal: false,
      reason_codes: ["replan_ceiling_reached"],
      current_plan_step: {
        step_id: "replan_step_1",
        objective: "Recover from repeated source fetch failures.",
        rationale: "The source path is stalled.",
        expected_outcome: "A viable source path.",
        status: "in_progress",
      },
      governance: {
        directive: "wait_human_review",
        summary: "The configured replan ceiling was reached.",
        selected_reason: "Three replans require operator direction.",
      },
      external_execution: {
        execution_intent: "execute_after_governance",
        retryable: false,
        attempt_count: 0,
        receipt_count: 0,
      },
      human_review: {
        pending_request: {
          request_id: "thread-trajectory:hitl",
          reason_codes: ["replan_ceiling_reached"],
          resume_state: "ready_tool_execution",
          allowed_decisions: ["approve_continue", "replan", "abort"],
        },
        trajectory_diagnostic: {
          pillar_name: "trajectory",
          required: true,
          mode: "active",
          level: "degraded",
          summary: "Trajectory intervention threshold reached.",
          reason_codes: ["replan_ceiling_reached"],
          facts: { replan_count: 3, consecutive_tool_failures: 3 },
        },
      },
    };

    render(<ReviewContextPanel taskGoal="Research current AI releases." snapshot={snapshot} />);

    // The primary, always-visible reason is a plain-language Italian explanation of the
    // reason code, not OPA's raw English audit sentence. The short label also appears
    // as a chip on the trajectory diagnostic breakdown below, so it is not unique.
    expect(screen.getAllByText("Troppi tentativi di ripianificazione").length).toBeGreaterThan(0);
    expect(
      screen.getByText(/L'attività ha cambiato strategia troppe volte/),
    ).toBeInTheDocument();
    // The raw OPA sentence is preserved for audit, inside the technical details.
    expect(screen.getByText("Three replans require operator direction.")).toBeInTheDocument();
    expect(screen.getByText(/Research current AI releases\./)).toBeInTheDocument();
    expect(
      screen.getAllByText(/Recover from repeated source fetch failures\./).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("Puoi scegliere tra: Approva, Ripianifica, Interrompi.")).toBeInTheDocument();
    expect(screen.queryByText("Testo prodotto")).not.toBeInTheDocument();
  });
});
