"""Framework-independent public domain view for canonical Layer 2 state."""

from .evidence import (
    ObservationLineage,
    build_evidence_selection_digest,
    build_evidence_snapshot_manifest,
    build_observation_lineage,
    digest_json,
    digest_text,
)
from .lifecycle import RuntimeFlag, RuntimeState, StepRecord
from .plan import EvidenceBurden, PlannerDecision, PlanStep, PlanStepStatus, semantic_plan_signature
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
    "ReplanCheckpoint",
    "RuntimeFlag",
    "RuntimeState",
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
