"""Framework-independent public domain view for canonical Layer 2 state."""

from .runtime_records import (
    ExecutedPlanStep,
    AgentWorkspace,
    ReplanCheckpoint,
    ScratchpadEntry,
    ScratchpadEntryType,
    StepValidation,
    StepCompletionCandidate,
    StepArtifact,
    ToolAttempt,
    ToolDisposition,
    ToolInvocation,
)
from .lifecycle import RuntimeFlag, RuntimeState, StepRecord
from .plan import PlanStep, PlanStepStatus, PlannerDecision, semantic_plan_signature
from .review import (
    ExternalCommunicationDraft,
    ExternalCommunicationPhase,
    ExternalCommunicationState,
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewExternalEdit,
    HumanReviewRequest,
)
from .serialization import (
    coerce_external_communication_draft,
    coerce_external_operation_proposal,
    coerce_human_review_external_edit,
    normalize_task_state_serialization,
)
from .task_state import TaskState
from .evidence import (
    ObservationLineage,
    build_evidence_selection_digest,
    build_evidence_snapshot_manifest,
    build_observation_lineage,
    digest_json,
    digest_text,
)

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
    "PlanStep",
    "PlanStepStatus",
    "PlannerDecision",
    "ReplanCheckpoint",
    "RuntimeFlag",
    "RuntimeState",
    "ScratchpadEntry",
    "ScratchpadEntryType",
    "StepRecord",
    "StepArtifact",
    "StepValidation",
    "StepCompletionCandidate",
    "TaskState",
    "ToolAttempt",
    "ToolDisposition",
    "ToolInvocation",
    "build_evidence_selection_digest",
    "build_evidence_snapshot_manifest",
    "build_observation_lineage",
    "coerce_external_communication_draft",
    "coerce_external_operation_proposal",
    "coerce_human_review_external_edit",
    "digest_json",
    "digest_text",
    "normalize_task_state_serialization",
    "semantic_plan_signature",
]
