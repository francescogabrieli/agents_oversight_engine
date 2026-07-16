"""Framework-independent public domain view for canonical Layer 2 state."""

from .evidence import EvidenceSourceKind, EvidenceSupportLevel, ObservedEvidence
from .runtime_records import (
    ExecutedPlanStep,
    ScratchpadEntry,
    ScratchpadEntryType,
    StepValidation,
    StepValidationDecision,
)
from .lifecycle import RuntimeFlag, RuntimeState, StepRecord
from .plan import PlanStep, PlanStepKind, PlanStepStatus, PlannerDecision
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
    "CapabilityAccessMode",
    "EvidenceSourceKind",
    "EvidenceSupportLevel",
    "ExecutedPlanStep",
    "ExternalCommunicationDraft",
    "ExternalCommunicationPhase",
    "ExternalCommunicationState",
    "HumanReviewDecision",
    "HumanReviewDecisionType",
    "HumanReviewExternalEdit",
    "HumanReviewRequest",
    "ObservedEvidence",
    "PlanStep",
    "PlanStepKind",
    "PlanStepStatus",
    "PlannerDecision",
    "RuntimeFlag",
    "RuntimeState",
    "ScratchpadEntry",
    "ScratchpadEntryType",
    "StepRecord",
    "StepValidation",
    "StepValidationDecision",
    "TaskState",
    "coerce_external_communication_draft",
    "coerce_human_review_external_edit",
    "normalize_task_state_serialization",
]
