from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol, TypeAlias

from layer1_intake import ExecutionIntent


class OperationBoundary(str, Enum):
    # Boundary is runtime-derived and is the canonical externality classification.
    LOCAL = "local"
    EVIDENCE_READ = "evidence_read"
    GOVERNED_EXTERNAL = "governed_external"


class ExternalOperationFamily(str, Enum):
    # Families select bounded normalizers without becoming a generic action framework.
    COMMUNICATION = "communication"
    DATABASE = "database"
    GENERIC = "generic"


class ExternalOperationEffect(str, Enum):
    # Effects are normalized facts consumed by deterministic policy.
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    TRUNCATE = "truncate"
    SEND = "send"
    PUBLISH = "publish"
    EXECUTE = "execute"
    UNKNOWN = "unknown"


class DestinationClass(str, Enum):
    # Destination classes are factual routing labels, not semantic safety judgments.
    INTERNAL = "internal"
    EXTERNAL = "external"
    PROHIBITED = "prohibited"
    UNKNOWN = "unknown"


class SQLStatementClass(str, Enum):
    # SQL statement classes come from the parsed AST, never substring authority.
    SELECT = "select"
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    TRUNCATE = "truncate"
    CREATE = "create"
    ALTER = "alter"
    DROP = "drop"
    MERGE = "merge"
    COMMAND = "command"
    MULTIPLE = "multiple"
    UNKNOWN = "unknown"


class ExternalExecutionStatus(str, Enum):
    # Execution outcomes distinguish deterministic failure from unknown external effect.
    EXECUTED = "executed"
    REJECTED_BEFORE_EXECUTION = "rejected_before_execution"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class ExternalExecutionAuthorizationSource(str, Enum):
    # Authorization records which digest-bound runtime authority enabled the request.
    OPA = "opa"
    HUMAN_REVIEW = "human_review"


class ExternalExecutionErrorCode(str, Enum):
    # Closed error codes keep receipts machine-readable without leaking connector detail.
    NONE = "none"
    MISSING_PROPOSAL = "missing_proposal"
    INVALID_EXECUTION_INTENT = "invalid_execution_intent"
    NOT_EXECUTION_CANDIDATE = "not_execution_candidate"
    AUTHORIZATION_MISSING = "authorization_missing"
    DIGEST_MISMATCH = "digest_mismatch"
    CAPABILITY_UNKNOWN = "capability_unknown"
    CAPABILITY_DISABLED = "capability_disabled"
    CONNECTOR_UNSUPPORTED = "connector_unsupported"
    READ_ONLY_VIOLATION = "read_only_violation"
    IDEMPOTENCY_KEY_MISSING = "idempotency_key_missing"
    DUPLICATE_EXECUTION = "duplicate_execution"
    INPUT_SCHEMA_INVALID = "input_schema_invalid"
    TOOL_NOT_ALLOWLISTED = "tool_not_allowlisted"
    CONNECTOR_TIMEOUT = "connector_timeout"
    CONNECTOR_FAILURE = "connector_failure"
    CONNECTOR_RESPONSE_INVALID = "connector_response_invalid"
    OUTCOME_INDETERMINATE = "outcome_indeterminate"
    RETRY_LIMIT_EXCEEDED = "retry_limit_exceeded"


@dataclass(frozen=True)
class ExternalOperationTarget:
    # Target keeps the destination fact explicit and bounded for judge and HITL.
    kind: str
    value: str
    destination_class: DestinationClass = DestinationClass.UNKNOWN


@dataclass(frozen=True)
class OperationFactsProvenance:
    # Provenance records which deterministic runtime components produced the facts.
    normalizer_id: str
    normalizer_version: str
    source_kind: str
    source_id: str | None = None
    registry_entry_id: str | None = None
    fact_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommunicationOperationPayload:
    # Communication payloads preserve exact outbound fields without semantic labeling.
    channel: str
    recipient: str | None
    subject: str | None
    body: str
    destination_class: DestinationClass
    cc: tuple[str, ...] = ()
    bcc: tuple[str, ...] = ()


DatabaseParameterValue: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True)
class DatabaseOperationParameter:
    # Parameters remain separate from SQL so adapters never concatenate actor values.
    name: str
    value: DatabaseParameterValue


@dataclass(frozen=True)
class DatabaseStatementDraft:
    # Actor providers may propose only a profile id, SQL text, and separate parameters.
    database_profile_id: str
    statement: str
    parameters: tuple[DatabaseOperationParameter, ...] = ()


@dataclass(frozen=True)
class DatabaseOperationPayload:
    # Database payloads retain parser-derived statement facts but are never executed here.
    sql: str
    normalized_sql: str | None
    statement_class: SQLStatementClass
    statement_count: int
    parse_valid: bool
    ambiguous: bool
    database_profile_id: str
    parameters: tuple[DatabaseOperationParameter, ...] = ()
    is_read_only_profile: bool = True


@dataclass(frozen=True)
class GenericPayloadField:
    # Generic payload fields stay bounded and typed instead of using an open primary dict.
    name: str
    value: str


@dataclass(frozen=True)
class GenericExternalOperationPayload:
    # Generic external operations expose only an action, target, and bounded string fields.
    action: str
    target: str
    fields: tuple[GenericPayloadField, ...] = ()


ExternalOperationPayload: TypeAlias = (
    CommunicationOperationPayload
    | DatabaseOperationPayload
    | GenericExternalOperationPayload
)


@dataclass(frozen=True)
class ExternalOperationProposal:
    # This is the canonical immutable proposal evaluated by OPA, the judge, and HITL.
    operation_id: str
    plan_step_id: str
    capability_id: str
    boundary: OperationBoundary
    operation_family: ExternalOperationFamily
    normalized_effect: ExternalOperationEffect
    execution_candidate: bool
    target: ExternalOperationTarget
    payload: ExternalOperationPayload
    payload_preview: str
    payload_digest: str
    facts_provenance: OperationFactsProvenance
    capability_profile_id: str | None = None
    capability_profile_version: str | None = None
    modifiable: bool = True

    # Reject mismatched or unbounded proposal material at construction time.
    def __post_init__(self) -> None:
        if len(self.payload_preview) > 4096:
            raise ValueError("External operation payload_preview exceeds 4096 characters.")
        expected_digest = compute_payload_digest(self.payload)
        if self.payload_digest != expected_digest:
            raise ValueError("External operation payload_digest does not match the payload.")

    # Keep operator summaries stable and independent from provider prose.
    def summary(self) -> str:
        return (
            f"{self.operation_family.value}/{self.normalized_effect.value} "
            f"toward {self.target.kind} '{self.target.value}'"
        )


@dataclass(frozen=True)
class JudgeFindingSnapshot:
    # Audit-safe copy of one semantic judge finding returned to the actor.
    category: str
    code: str
    severity: str
    summary: str
    evidence_refs: tuple[str, ...] = ()
    task_requirement_kind: str | None = None
    task_requirement_index: int | None = None
    task_requirement_quote: str | None = None
    payload_field: str | None = None
    payload_excerpt: str | None = None


@dataclass(frozen=True)
class GovernanceActionFeedback:
    # Findings are audit-safe revision instructions, not hidden reasoning.
    operation_id: str
    invalidated_payload_digest: str
    finding_codes: tuple[str, ...]
    summary: str
    revision_index: int
    invalidated_evidence_selection_digest: str | None = None
    # Full typed findings and judge summary so a revision can correct each finding.
    findings: tuple[JudgeFindingSnapshot, ...] = ()
    judge_summary: str | None = None
    judge_rationale: str | None = None


@dataclass(frozen=True)
class ExternalOperationPayloadEdit:
    # HITL edits replace the full typed payload and therefore produce a new digest.
    payload: ExternalOperationPayload
    note: str | None = None


@dataclass(frozen=True)
class ExternalExecutionRequest:
    # The request is created only after governance and never grants itself authority.
    operation_id: str
    capability_id: str
    proposal: ExternalOperationProposal
    current_payload_digest: str
    authorized_payload_digest: str
    execution_intent: ExecutionIntent
    idempotency_key: str
    authorization_source: ExternalExecutionAuthorizationSource
    reviewer_id: str | None = None
    governance_decision_reference: str | None = None
    attempt_scope: str = "initial"

    # Reject request drift before an infrastructure adapter can observe it.
    def __post_init__(self) -> None:
        if self.operation_id != self.proposal.operation_id:
            raise ValueError("External execution request operation_id does not match proposal.")
        if self.capability_id != self.proposal.capability_id:
            raise ValueError("External execution request capability_id does not match proposal.")
        if self.current_payload_digest != self.proposal.payload_digest:
            raise ValueError("External execution current digest does not match proposal.")


@dataclass(frozen=True)
class ExternalExecutionReceipt:
    # Receipts are audit-safe and contain no full outbound payload or credentials.
    operation_id: str
    capability_id: str
    payload_digest: str
    status: ExternalExecutionStatus
    idempotency_key: str
    started_at: datetime
    completed_at: datetime
    external_reference: str | None = None
    summary: str = ""
    error_code: ExternalExecutionErrorCode = ExternalExecutionErrorCode.NONE
    retryable: bool = False
    connector_kind: str | None = None
    connector_id: str | None = None
    server_id: str | None = None
    tool_name: str | None = None
    connector_metadata: tuple[GenericPayloadField, ...] = ()
    attempt_number: int = 1

    # Preserve the former boolean projection without collapsing richer outcomes.
    @property
    def executed(self) -> bool:
        return self.status == ExternalExecutionStatus.EXECUTED


class ExternalOperationExecutorPort(Protocol):
    # Executors invoke only a pre-authorized typed request and never decide governance.
    def execute(self, request: ExternalExecutionRequest) -> ExternalExecutionReceipt: ...


# Preserve the former public name while callers migrate to the receipt contract.
ExternalOperationExecutionResult = ExternalExecutionReceipt


# Compute a deterministic digest over the typed normalized payload only.
def compute_payload_digest(payload: ExternalOperationPayload) -> str:
    canonical = json.dumps(
        _jsonable_payload(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# Convert the discriminated dataclass payload into canonical JSON primitives.
def _jsonable_payload(payload: ExternalOperationPayload) -> dict[str, object]:
    raw = asdict(payload)
    normalized: dict[str, object] = {"payload_type": type(payload).__name__}
    for key, value in raw.items():
        if isinstance(value, Enum):
            normalized[key] = value.value
        elif isinstance(value, tuple):
            normalized[key] = [
                {
                    field_key: (
                        field_value.value
                        if isinstance(field_value, Enum)
                        else field_value
                    )
                    for field_key, field_value in asdict(item).items()
                }
                if hasattr(item, "__dataclass_fields__")
                else item
                for item in value
            ]
        else:
            normalized[key] = value
    return normalized


__all__ = [
    "CommunicationOperationPayload",
    "DatabaseOperationPayload",
    "DatabaseOperationParameter",
    "DatabaseStatementDraft",
    "DatabaseParameterValue",
    "DestinationClass",
    "ExternalExecutionAuthorizationSource",
    "ExternalExecutionErrorCode",
    "ExternalExecutionReceipt",
    "ExternalExecutionRequest",
    "ExternalExecutionStatus",
    "ExternalOperationEffect",
    "ExternalOperationExecutionResult",
    "ExternalOperationExecutorPort",
    "ExternalOperationFamily",
    "ExternalOperationPayload",
    "ExternalOperationPayloadEdit",
    "ExternalOperationProposal",
    "ExternalOperationTarget",
    "GenericExternalOperationPayload",
    "GenericPayloadField",
    "GovernanceActionFeedback",
    "JudgeFindingSnapshot",
    "OperationBoundary",
    "OperationFactsProvenance",
    "SQLStatementClass",
    "compute_payload_digest",
]
