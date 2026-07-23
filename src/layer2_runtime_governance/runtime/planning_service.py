from layer1_intake import DomainType

from ..planning import PlannerPort
from ..domain import (
    PlanStep,
    PlanStepKind,
    PlanStepStatus,
    RuntimeState,
    TaskState,
)
from ..domain.evidence_policy import evidence_support_level_rank
from ..tool_contract import EvidenceSupportLevel

# Nodes.py contains the executable Layer 2 business logic.
# At this stage the runtime performs bootstrap, planning, and the actor/tool loop.
from .lifecycle_service import _append_step_record

# Maintain a proportional typed semantic plan for the runtime.
def planner(state: TaskState, *, planner_service: PlannerPort) -> TaskState:
    if state.current_state != RuntimeState.PLANNING:
        _append_step_record(
            state,
            node_name="planner",
            summary="Planning skipped because the runtime is not currently in a planning state.",
        )
        return state

    decision = planner_service.plan(task_state=state)
    planned_steps = _compact_external_communication_plan(
        task_state=state,
        plan_steps=decision.plan_steps,
    )
    planned_steps = _compact_small_source_summary_plan(
        task_state=state,
        plan_steps=planned_steps,
    )
    state.plan_steps = _enforce_plan_step_budget(
        plan_steps=planned_steps,
        max_steps=state.execution_policy.budget.max_steps,
    )
    state.current_plan_step_index = 0 if state.plan_steps else None
    state.planner_rationale = decision.rationale
    state.planner_name = decision.planner_name
    state.planner_prompt_version = decision.prompt_version

    if state.current_plan_step is not None:
        state.current_plan_step.status = PlanStepStatus.IN_PROGRESS

    planner_summary = "Initial semantic full plan created from the current task state."
    if planned_steps != decision.plan_steps:
        planner_summary = (
            "Initial semantic full plan created from the current task state and "
            "normalized to the task scope."
        )
    elif len(state.plan_steps) != len(planned_steps):
        planner_summary = (
            "Initial semantic full plan created from the current task state and "
            "trimmed to the configured semantic step budget."
        )

    _append_step_record(
        state,
        node_name="planner",
        summary=planner_summary,
    )
    return state

# Maintain a proportional typed semantic plan for the runtime.
def _enforce_plan_step_budget(
    *,
    plan_steps: list[PlanStep],
    max_steps: int | None,
) -> list[PlanStep]:
    if max_steps is None:
        return list(plan_steps)
    return list(plan_steps[:max_steps])


# Collapse governance-like communication meta-steps while retaining genuine evidence work.
def _compact_external_communication_plan(
    *,
    task_state: TaskState,
    plan_steps: list[PlanStep],
) -> list[PlanStep]:
    if task_state.task_spec.domain_type != DomainType.EXTERNAL_COMMUNICATION:
        return plan_steps

    evidence_steps = [
        step
        for step in plan_steps
        if step.step_kind
        in {
            PlanStepKind.SOURCE_DISCOVERY,
            PlanStepKind.DETAILED_EXTRACTION,
        }
        or (
            step.evidence_requirement is not None
            and step.evidence_requirement != EvidenceSupportLevel.LOCAL_ONLY
        )
    ]
    evidence_step = max(
        evidence_steps,
        key=lambda step: evidence_support_level_rank(
            _external_step_evidence_requirement(step=step)
        ),
        default=None,
    )
    required_level = (
        _external_step_evidence_requirement(step=evidence_step)
        if evidence_step is not None
        else EvidenceSupportLevel.LOCAL_ONLY
    )
    draft_step = PlanStep(
        step_id="step_2" if evidence_step is not None else "step_1",
        objective=(
            "Produce one complete requested external communication draft with explicit "
            "channel, recipient, subject, and body. Preserve the authorized task semantics "
            "and constraints without sending the communication."
        ),
        rationale=(
            "Draft construction is one semantic operation; deterministic policy and the "
            "semantic judge evaluate the normalized proposal after the actor produces it."
        ),
        expected_outcome=(
            "A complete typed external communication draft ready for runtime normalization "
            "and governance, with no external side effect."
        ),
        step_kind=PlanStepKind.FINAL_SYNTHESIS,
        evidence_requirement=required_level,
        claim_precision_ceiling=(
            evidence_step.claim_precision_ceiling
            if evidence_step is not None
            and evidence_step.claim_precision_ceiling is not None
            else required_level
        ),
    )
    if evidence_step is None:
        return [draft_step]
    return [
        PlanStep(
            step_id="step_1",
            objective=evidence_step.objective,
            rationale=evidence_step.rationale,
            expected_outcome=evidence_step.expected_outcome,
            step_kind=evidence_step.step_kind,
            evidence_requirement=required_level,
            claim_precision_ceiling=evidence_step.claim_precision_ceiling,
        ),
        draft_step,
    ]


# Resolve the strongest explicit requirement represented by one retained evidence step.
def _external_step_evidence_requirement(
    *,
    step: PlanStep,
) -> EvidenceSupportLevel:
    if step.evidence_requirement is not None:
        return step.evidence_requirement
    if step.step_kind == PlanStepKind.DETAILED_EXTRACTION:
        return EvidenceSupportLevel.DETAILED_EXTRACTED
    return EvidenceSupportLevel.SOURCE_DISCOVERY


# Maintain a proportional typed semantic plan for the runtime.
def _compact_small_source_summary_plan(
    *,
    task_state: TaskState,
    plan_steps: list[PlanStep],
) -> list[PlanStep]:
    if len(plan_steps) <= 2:
        return plan_steps
    if not _task_is_small_source_summary(task_state=task_state):
        return plan_steps
    if not _plan_looks_like_extraction_pipeline(plan_steps=plan_steps):
        return plan_steps

    return [
        PlanStep(
            step_id="step_1",
            objective=(
                "Identify official sources and collect only the high-level evidence needed "
                "for the requested short summary."
            ),
            rationale=(
                "The task asks for official references and a brief answer, so source selection "
                "and minimal evidence gathering should stay in one bounded semantic step."
            ),
            expected_outcome=(
                "A compact set of official source references plus enough high-level source "
                "content or metadata to support a concise summary."
            ),
            step_kind=PlanStepKind.SOURCE_DISCOVERY,
            evidence_requirement=EvidenceSupportLevel.SOURCE_DISCOVERY,
            claim_precision_ceiling=EvidenceSupportLevel.HIGH_LEVEL_SUMMARY,
        ),
        PlanStep(
            step_id="step_2",
            objective=(
                "Produce the short source-grounded summary with references, staying at the "
                "precision level supported by the observed evidence."
            ),
            rationale=(
                "A short user-facing summary should not become a separate extraction pipeline "
                "unless the user asks for granular legal or technical detail."
            ),
            expected_outcome=(
                "A brief summary and official source list, with no unsupported thresholds, "
                "article-level claims, or granular obligations."
            ),
            step_kind=PlanStepKind.FINAL_SYNTHESIS,
            evidence_requirement=EvidenceSupportLevel.HIGH_LEVEL_SUMMARY,
            claim_precision_ceiling=EvidenceSupportLevel.HIGH_LEVEL_SUMMARY,
        ),
    ]

# Maintain a proportional typed semantic plan for the runtime.
def _task_is_small_source_summary(*, task_state: TaskState) -> bool:
    task_text = " ".join(
        part
        for part in (
            task_state.task_spec.goal,
            task_state.task_spec.context,
            " ".join(task_state.task_spec.constraints),
            " ".join(task_state.task_spec.success_criteria),
        )
        if part
    ).lower()
    source_markers = ("source", "sources", "reference", "references", "official")
    summary_markers = ("short summary", "brief summary", "concise summary", "short answer")
    detailed_markers = (
        "threshold",
        "thresholds",
        "article-level",
        "full legal text",
        "compliance mapping",
        "detailed analysis",
    )
    return (
        any(marker in task_text for marker in source_markers)
        and any(marker in task_text for marker in summary_markers)
        and not any(marker in task_text for marker in detailed_markers)
    )

# Maintain a proportional typed semantic plan for the runtime.
def _plan_looks_like_extraction_pipeline(*, plan_steps: list[PlanStep]) -> bool:
    plan_text = " ".join(
        " ".join((step.objective, step.rationale, step.expected_outcome))
        for step in plan_steps
    ).lower()
    return (
        any(marker in plan_text for marker in ("source", "sources", "references"))
        and any(marker in plan_text for marker in ("extract", "extraction", "capture"))
        and any(marker in plan_text for marker in ("summary", "summar", "synthesis", "draft"))
    )

__all__ = [
    "planner",
    "_enforce_plan_step_budget",
    "_compact_small_source_summary_plan",
    "_compact_external_communication_plan",
    "_external_step_evidence_requirement",
    "_task_is_small_source_summary",
    "_plan_looks_like_extraction_pipeline"
]
