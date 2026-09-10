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
      "Segnala chiarimento necessario",
      "Interrompi",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "Invia decisione" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Approva" }));
    await user.click(screen.getByRole("button", { name: "Invia decisione: Approva" }));

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

  // A bare flagged token is not adjudicable; the reviewer needs the sentence it was
  // written in to decide whether the value is wrong or merely phrased differently.
  it("shows each flagged value with the sentence it appears in", () => {
    const snapshot: DashboardSnapshot = {
      runtime_state: "waiting_human_review",
      resume_state: "acting",
      has_pending_review: true,
      is_terminal: false,
      reason_codes: ["evidence_anchoring_requires_review"],
      external_execution: {
        execution_intent: "execute_after_governance",
        retryable: false,
        attempt_count: 0,
        receipt_count: 0,
      },
      human_review: {
        pending_request: {
          request_id: "thread-anchor:hitl",
          reason_codes: ["evidence_anchoring_requires_review"],
          resume_state: "acting",
          allowed_decisions: ["approve_continue", "modify_and_continue", "abort"],
        },
        evidence_diagnostic: {
          pillar_name: "evidence",
          required: true,
          mode: "active",
          level: "unscored",
          summary: "Evidence structural controls evaluated.",
          reason_codes: ["unanchored_literals_in_selection"],
          facts: {
            unanchored_literal_excerpts: [
              {
                value: "82,5%",
                excerpt: "Il rollout ha ridotto i costi del 82,5% nel trimestre.",
                source_matches: [
                  {
                    locator: "https://source-a.org/notes",
                    excerpt: "it improved average accuracy by up to 82.5 percent while reducing cost.",
                  },
                ],
              },
              { value: "99%", excerpt: "Copertura del 99% dei casi.", source_matches: [] },
            ],
          },
        },
      },
    };

    render(<ReviewContextPanel taskGoal="Prepare a sourced briefing." snapshot={snapshot} />);

    expect(screen.getByText("82,5%")).toBeInTheDocument();
    expect(
      screen.getByText(/Il rollout ha ridotto i costi del 82,5% nel trimestre\./),
    ).toBeInTheDocument();
    // The source passage is what makes the value adjudicable: the agent wrote "82,5%",
    // the source wrote "82.5 percent" - the same quantity the matcher did not recognize.
    expect(
      screen.getByText(/it improved average accuracy by up to 82\.5 percent/),
    ).toBeInTheDocument();
    // A value found in no source is the decisive opposite signal, stated explicitly.
    expect(
      screen.getByText(/Nessun riscontro di questo valore in nessuna fonte/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Valori segnalati/)).toBeInTheDocument();
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

  // Reproduces the shape of an evidence-anchoring stop late in a run (benchmark
  // thread B23): OPA selected the resubmission reason, while the evidence concern and
  // the capability gate stayed open, and the operator had already decided four times.
  it("names the concerns that stay open and the decisions already taken", () => {
    const snapshot: DashboardSnapshot = {
      runtime_state: "waiting_human_review",
      resume_state: "acting",
      has_pending_review: true,
      is_terminal: false,
      reason_codes: ["unchanged_candidate_resubmission"],
      governance: {
        directive: "wait_human_review",
        summary: "The actor resubmitted the same external candidate.",
        selected_reason: "The actor resubmitted the same external candidate.",
        rejected_alternatives: {
          capability_approval_required: "The capability approval mode requires a typed authorization.",
          evidence_anchoring_requires_review: "Literal claims are absent from every observation.",
        },
      },
      external_execution: {
        execution_intent: "execute_after_governance",
        retryable: false,
        attempt_count: 0,
        receipt_count: 0,
      },
      human_review: {
        pending_request: {
          request_id: "b23:human_review:86:step_7",
          reason_codes: ["unchanged_candidate_resubmission"],
          resume_state: "acting",
          operation_payload_digest: "2b02b4e3",
          allowed_decisions: ["modify_and_continue", "replan", "abort"],
        },
        decision_history: [
          { request_id: "b23:human_review:77:step_7", decision_type: "approve_continue", approved_operation_digest: "2b02b4e3" },
          { request_id: "b23:human_review:80:step_7", decision_type: "approve_continue", approved_operation_digest: "2b02b4e3" },
          { request_id: "b23:human_review:83:step_7", decision_type: "modify_and_continue", approved_operation_digest: null },
        ],
        evidence_diagnostic: {
          pillar_name: "evidence",
          required: true,
          mode: "active",
          level: "unscored",
          summary: "Concrete structural evidence facts collected across 5 families.",
          reason_codes: ["unanchored_literals_in_selection"],
          facts: {
            retrieved_independent_sources_count: 3,
            min_independent_sources_required: 3,
            independent_sources_satisfied: true,
            selected_evidence_ref_count: 4,
            selected_evidence_refs_valid: true,
            retrieved_content_satisfied: true,
            evidence_request_count: 3,
            evidence_request_ceiling_reached: false,
            unanchored_literals_in_selection: ["10/08/2026"],
          },
        },
        trajectory_diagnostic: {
          pillar_name: "trajectory",
          required: true,
          mode: "active",
          level: "observed",
          summary: "Whole-run trajectory facts collected.",
          reason_codes: ["unchanged_candidate_resubmission"],
          facts: { replan_count: 0, tool_call_count: 12, failed_tool_call_count: 0, unchanged_resubmission: true },
        },
      },
    };

    render(<ReviewContextPanel taskGoal="Send a sourced briefing." snapshot={snapshot} />);

    // The evidence concern is not the selected reason, so without this section the
    // operator cannot tell why approving keeps bringing the run back to the same stop.
    expect(screen.getByText("Altre criticità ancora aperte")).toBeInTheDocument();
    expect(screen.getByText("Valori nel testo non riscontrati nelle fonti")).toBeInTheDocument();
    expect(screen.getByText("Serve la tua autorizzazione esplicita")).toBeInTheDocument();

    expect(screen.getByText(/Decisioni già prese su questa attività — 3/)).toBeInTheDocument();
    expect(screen.getByText(/Hai già deciso 2 volte su questa identica proposta/)).toBeInTheDocument();

    // The deciding pillar is named as such; the other one is reported as merely run.
    expect(screen.getByText("causa della fermata")).toBeInTheDocument();
    expect(screen.getByText("verificata")).toBeInTheDocument();

    // Evidence substance is readable without opening the 38-row fact table.
    expect(screen.getByText("3 su 3 richieste")).toBeInTheDocument();
    expect(screen.getByText("1 (10/08/2026)")).toBeInTheDocument();
    expect(screen.getByText("Richieste di più prove già fatte")).toBeInTheDocument();
    expect(screen.getByText("12, di cui 0 fallite")).toBeInTheDocument();
  });

  // B22 stopped on a clarification and the panel showed only "Serve un chiarimento":
  // the agent's actual question never reached the operator.
  it("shows the agent's own question on a clarification stop", () => {
    const snapshot: DashboardSnapshot = {
      runtime_state: "waiting_human_review",
      resume_state: "acting",
      has_pending_review: true,
      is_terminal: false,
      reason_codes: ["ask_clarification_required"],
      external_execution: {
        execution_intent: "execute_after_governance",
        retryable: false,
        attempt_count: 0,
        receipt_count: 0,
      },
      human_review: {
        pending_request: {
          request_id: "b22:human_review:40:replan_step_1",
          actor_result_type: "ask_clarification",
          actor_summary: "Devo accettare come valide anche le fonti senza data esplicita?",
          actor_blocking_reason: "La finestra temporale non è decidibile dalle evidenze raccolte.",
          reason_codes: ["ask_clarification_required"],
          resume_state: "acting",
          allowed_decisions: ["ask_clarification", "abort"],
        },
      },
    };

    render(<ReviewContextPanel taskGoal="Prepare a sourced briefing." snapshot={snapshot} />);

    expect(screen.getByText("L'agente ha chiesto un chiarimento")).toBeInTheDocument();
    expect(
      screen.getByText("Devo accettare come valide anche le fonti senza data esplicita?"),
    ).toBeInTheDocument();
    expect(screen.getByText("Motivo del blocco:")).toBeInTheDocument();
  });

  // A run that emits ask_clarification with a placeholder reason is a real failure mode
  // (B22 did exactly this): show it as such instead of dressing it up as a motivation.
  it("marks a placeholder blocking reason as such", () => {
    const snapshot: DashboardSnapshot = {
      runtime_state: "waiting_human_review",
      has_pending_review: true,
      is_terminal: false,
      reason_codes: ["ask_clarification_required"],
      external_execution: {
        execution_intent: "execute_after_governance",
        retryable: false,
        attempt_count: 0,
        receipt_count: 0,
      },
      human_review: {
        pending_request: {
          request_id: "b22:human_review:40:replan_step_1",
          actor_result_type: "ask_clarification",
          actor_summary: "Nessuna richiesta necessaria: procedo a completare lo step corrente.",
          actor_blocking_reason: "placeholder_non_bloccante",
          reason_codes: ["ask_clarification_required"],
          allowed_decisions: ["ask_clarification", "abort"],
        },
      },
    };

    render(<ReviewContextPanel taskGoal="Prepare a sourced briefing." snapshot={snapshot} />);

    expect(
      screen.getByText("Motivo dichiarato (nessun motivo reale indicato):"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Nessuna richiesta necessaria/)).toBeInTheDocument();
  });
});
