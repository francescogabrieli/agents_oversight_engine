"""Framework-independent public domain view for canonical Layer 2 state."""

from .evidence import (
    ObservationLineage,
    build_evidence_selection_digest,
    build_evidence_snapshot_manifest,
    build_observation_lineage,
    digest_json,
    digest_text,
)
from .lifecycle import RuntimeFlag, RuntimeState, StepRecord, TerminationReason
from .plan import (
    EvidenceBurden,
    PlannerDecision,
    PlanStep,
    PlanStepStatus,
    apply_task_evidence_floor,
    derive_evidence_obligation_floor,
    derive_task_evidence_floor,
    lift_evidence_burden_to_floor,
    project_evidence_burden,
    semantic_plan_signature,
    validate_external_effect_plan_contract,
    validate_plan_step_evidence_contract,
    validate_plan_steps_evidence_contract,
    validate_replacement_evidence_obligation,
)
from .review import (
    ExternalCommunicationDraft,
    ExternalCommunicationPhase,
    ExternalCommunicationState,
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewExternalEdit,
    HumanReviewRequest,
    set_external_communication_phase,
)
from .review_payload import coerce_human_review_decision_payload
from .runtime_records import (
    AgentWorkspace,
    ExecutedPlanStep,
    ReplanCheckpoint,
    ScratchpadEntry,
    ScratchpadEntryType,
    StepArtifact,
    StepCompletionCandidate,
    StepValidation,
    ToolAttempt,
    ToolDisposition,
    ToolInvocation,
)
from .serialization import (
    coerce_external_communication_draft,
    coerce_external_operation_proposal,
    coerce_human_review_external_edit,
    normalize_task_state_serialization,
)
from .task_state import TaskState

_LAZY_ACTOR_EXPORTS = {"ActorResult", "ActorResultType", "CapabilityAccessMode"}


# Load actor contracts lazily so actor_contract can import the state compatibility facade safely.
def __getattr__(name: str):
    if name not in _LAZY_ACTOR_EXPORTS:
        raise AttributeError(name)
    from . import execution

    return getattr(execution, name)

__all__ = [
    "ActorResult",
    "ActorResultType",
    "AgentWorkspace",
    "CapabilityAccessMode",
    "ExecutedPlanStep",
    "ExternalCommunicationDraft",
    "ExternalCommunicationPhase",
    "ExternalCommunicationState",
    "HumanReviewDecision",
    "HumanReviewDecisionType",
    "HumanReviewExternalEdit",
    "HumanReviewRequest",
    "ObservationLineage",
    "EvidenceBurden",
    "PlanStep",
    "PlanStepStatus",
    "PlannerDecision",
    "apply_task_evidence_floor",
    "derive_evidence_obligation_floor",
    "derive_task_evidence_floor",
    "lift_evidence_burden_to_floor",
    "project_evidence_burden",
    "validate_external_effect_plan_contract",
    "validate_plan_step_evidence_contract",
    "validate_plan_steps_evidence_contract",
    "validate_replacement_evidence_obligation",
    "ReplanCheckpoint",
    "RuntimeFlag",
    "RuntimeState",
    "TerminationReason",
    "ScratchpadEntry",
    "ScratchpadEntryType",
    "StepArtifact",
    "StepCompletionCandidate",
    "StepRecord",
    "StepValidation",
    "TaskState",
    "ToolAttempt",
    "ToolDisposition",
    "ToolInvocation",
    "build_evidence_selection_digest",
    "build_evidence_snapshot_manifest",
    "build_observation_lineage",
    "coerce_external_communication_draft",
    "coerce_external_operation_proposal",
    "coerce_human_review_decision_payload",
    "coerce_human_review_external_edit",
    "digest_json",
    "digest_text",
    "normalize_task_state_serialization",
    "semantic_plan_signature",
    "set_external_communication_phase",
]
