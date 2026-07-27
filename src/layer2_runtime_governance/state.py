"""Compatibility facade for canonical Layer 2 domain state contracts."""

from .domain.serialization import (
    coerce_external_communication_draft,
    coerce_human_review_external_edit,
    normalize_task_state_serialization,
)
from .domain.lifecycle import (
    RuntimeState,
    RuntimeFlag,
    StepRecord,
)
from .domain.plan import (
    PlanStepStatus,
    PlanStep,
    PlannerDecision,
)
from .domain.review import (
    HumanReviewDecisionType,
    ExternalCommunicationPhase,
    ExternalCommunicationDraft,
    HumanReviewExternalEdit,
    HumanReviewRequest,
    HumanReviewDecision,
    ExternalCommunicationState,
)
from .domain.runtime_records import (
    ScratchpadEntryType,
    ExecutedPlanStep,
    ScratchpadEntry,
    StepValidation,
)
from .domain.task_state import (
    TaskState,
)

__all__ = [
    "coerce_external_communication_draft",
    "coerce_human_review_external_edit",
    "RuntimeState",
    "RuntimeFlag",
    "StepRecord",
    "PlanStepStatus",
    "PlanStep",
    "PlannerDecision",
    "ScratchpadEntryType",
    "ExecutedPlanStep",
    "ScratchpadEntry",
    "StepValidation",
    "HumanReviewDecisionType",
    "ExternalCommunicationPhase",
    "ExternalCommunicationDraft",
    "HumanReviewExternalEdit",
    "HumanReviewRequest",
    "HumanReviewDecision",
    "ExternalCommunicationState",
    "TaskState",
    "normalize_task_state_serialization"
]
