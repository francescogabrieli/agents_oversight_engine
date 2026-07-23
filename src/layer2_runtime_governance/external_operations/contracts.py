from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import TypeAlias


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
class GovernanceActionFeedback:
    # Findings are audit-safe revision instructions, not hidden reasoning.
    operation_id: str
    invalidated_payload_digest: str
    finding_codes: tuple[str, ...]
    summary: str
    revision_index: int


@dataclass(frozen=True)
class ExternalOperationPayloadEdit:
    # HITL edits replace the full typed payload and therefore produce a new digest.
    payload: ExternalOperationPayload
    note: str | None = None


@dataclass(frozen=True)
class ExternalOperationExecutionResult:
    # The execution seam is explicit even though production side effects remain deferred.
    operation_id: str
    payload_digest: str
    executed: bool
    summary: str
    external_reference: str | None = None


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
    "DestinationClass",
    "ExternalOperationEffect",
    "ExternalOperationExecutionResult",
    "ExternalOperationFamily",
    "ExternalOperationPayload",
    "ExternalOperationPayloadEdit",
    "ExternalOperationProposal",
    "ExternalOperationTarget",
    "GenericExternalOperationPayload",
    "GenericPayloadField",
    "GovernanceActionFeedback",
    "OperationBoundary",
    "OperationFactsProvenance",
    "SQLStatementClass",
    "compute_payload_digest",
]
