from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from email.utils import getaddresses
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from .contracts import (
    CommunicationOperationPayload,
    DatabaseOperationPayload,
    DestinationClass,
    ExternalOperationEffect,
    ExternalOperationFamily,
    ExternalOperationProposal,
    ExternalOperationTarget,
    GenericExternalOperationPayload,
    GenericPayloadField,
    OperationBoundary,
    OperationFactsProvenance,
    SQLStatementClass,
    compute_payload_digest,
)

if TYPE_CHECKING:
    from ..domain.review import ExternalCommunicationDraft

_NORMALIZER_VERSION = "external_operation_normalizers.v1"


# Normalize an external communication without making semantic claims about its tone.
def normalize_communication_operation(
    *,
    operation_id: str,
    plan_step_id: str,
    capability_id: str,
    draft: ExternalCommunicationDraft,
    execution_candidate: bool,
    prohibited_domains: Iterable[str] = (),
    capability_profile_id: str = "communication.default",
    capability_profile_version: str = "1",
) -> ExternalOperationProposal:
    channel = _single_line(draft.channel).lower() or "external_communication"
    recipient = _normalize_recipient(draft.recipient)
    subject = _optional_single_line(draft.subject)
    body = _bounded_body(draft.body or "")
    cc = tuple(
        address
        for value in draft.cc
        for address in _parsed_recipient_addresses(value)
    )
    bcc = tuple(
        address
        for value in draft.bcc
        for address in _parsed_recipient_addresses(value)
    )
    all_recipients = tuple(
        item
        for item in (recipient, *cc, *bcc)
        if item
    )
    destination_class = _classify_destination(
        recipients=all_recipients,
        prohibited_domains=tuple(item.lower().strip() for item in prohibited_domains),
    )
    payload = CommunicationOperationPayload(
        channel=channel,
        recipient=recipient,
        subject=subject,
        body=body,
        destination_class=destination_class,
        cc=cc,
        bcc=bcc,
    )
    target_value = recipient or channel
    return ExternalOperationProposal(
        operation_id=operation_id,
        plan_step_id=plan_step_id,
        capability_id=capability_id,
        boundary=OperationBoundary.GOVERNED_EXTERNAL,
        operation_family=ExternalOperationFamily.COMMUNICATION,
        normalized_effect=ExternalOperationEffect.SEND,
        execution_candidate=execution_candidate,
        target=ExternalOperationTarget(
            kind="recipient" if recipient else "channel",
            value=target_value,
            destination_class=destination_class,
        ),
        payload=payload,
        payload_preview=_bounded_preview(
            "\n".join(
                part
                for part in (
                    f"To: {recipient}" if recipient else None,
                    f"Cc: {', '.join(cc)}" if cc else None,
                    f"Bcc: {', '.join(bcc)}" if bcc else None,
                    f"Subject: {subject}" if subject else None,
                    body,
                )
                if part
            )
        ),
        payload_digest=compute_payload_digest(payload),
        capability_profile_id=capability_profile_id,
        capability_profile_version=capability_profile_version,
        facts_provenance=OperationFactsProvenance(
            normalizer_id="communication",
            normalizer_version=_NORMALIZER_VERSION,
            source_kind="external_communication_draft",
            source_id=plan_step_id,
        ),
    )


# Parse one SQL statement with sqlglot and derive its class/effect from the AST.
def normalize_sql_operation(
    *,
    operation_id: str,
    plan_step_id: str,
    capability_id: str,
    sql: str,
    database_target: str,
    database_profile_id: str = "enterprise_db.read_only",
    capability_profile_version: str = "1",
    dialect: str | None = None,
    parameters=(),
    is_read_only_profile: bool | None = None,
    execution_candidate: bool = True,
) -> ExternalOperationProposal:
    raw_sql = sql.strip()
    expressions: list[exp.Expression] = []
    parse_valid = True
    try:
        expressions = list(parse(raw_sql, read=dialect))
    except (ParseError, ValueError):
        parse_valid = False

    statement_count = len(expressions)
    ambiguous = not parse_valid or statement_count != 1
    if statement_count > 1:
        statement_class = SQLStatementClass.MULTIPLE
        normalized_sql = None
    elif statement_count == 1 and parse_valid:
        expression = expressions[0]
        statement_class = _sql_statement_class(expression)
        normalized_sql = expression.sql(dialect=dialect, pretty=False)
        ambiguous = statement_class in {
            SQLStatementClass.COMMAND,
            SQLStatementClass.UNKNOWN,
        }
    else:
        statement_class = SQLStatementClass.UNKNOWN
        normalized_sql = None

    resolved_read_only = (
        is_read_only_profile
        if is_read_only_profile is not None
        else _is_database_profile_read_only(database_profile_id)
    )

    effect = _effect_for_statement_class(statement_class)
    payload = DatabaseOperationPayload(
        sql=raw_sql,
        normalized_sql=normalized_sql,
        statement_class=statement_class,
        statement_count=statement_count,
        parse_valid=parse_valid,
        ambiguous=ambiguous,
        database_profile_id=database_profile_id,
        parameters=tuple(parameters),
        is_read_only_profile=resolved_read_only,
    )
    return ExternalOperationProposal(
        operation_id=operation_id,
        plan_step_id=plan_step_id,
        capability_id=capability_id,
        boundary=OperationBoundary.GOVERNED_EXTERNAL,
        operation_family=ExternalOperationFamily.DATABASE,
        normalized_effect=effect,
        execution_candidate=execution_candidate,
        target=ExternalOperationTarget(
            kind="database",
            value=_single_line(database_target) or "unknown_database",
            destination_class=DestinationClass.EXTERNAL,
        ),
        payload=payload,
        payload_preview=_bounded_preview(normalized_sql or raw_sql),
        payload_digest=compute_payload_digest(payload),
        capability_profile_id=database_profile_id,
        capability_profile_version=capability_profile_version,
        facts_provenance=OperationFactsProvenance(
            normalizer_id="sqlglot_ast",
            normalizer_version=_NORMALIZER_VERSION,
            source_kind="runtime_action",
            source_id=plan_step_id,
            fact_notes=("single_statement_required",),
        ),
    )


# Normalize an unknown external action conservatively as governed external.
def normalize_generic_external_operation(
    *,
    operation_id: str,
    plan_step_id: str,
    capability_id: str,
    action: str,
    target: str,
    fields: Iterable[tuple[str, str]] = (),
    effect: ExternalOperationEffect = ExternalOperationEffect.UNKNOWN,
    execution_candidate: bool = True,
    capability_profile_id: str = "generic.default",
    capability_profile_version: str = "1",
) -> ExternalOperationProposal:
    normalized_fields = tuple(
        GenericPayloadField(
            name=_single_line(name)[:80],
            value=_single_line(value)[:1000],
        )
        for name, value in tuple(fields)[:20]
    )
    payload = GenericExternalOperationPayload(
        action=_single_line(action)[:160] or "external_action",
        target=_single_line(target)[:512] or "unknown_target",
        fields=normalized_fields,
    )
    return ExternalOperationProposal(
        operation_id=operation_id,
        plan_step_id=plan_step_id,
        capability_id=capability_id,
        boundary=OperationBoundary.GOVERNED_EXTERNAL,
        operation_family=ExternalOperationFamily.GENERIC,
        normalized_effect=effect,
        execution_candidate=execution_candidate,
        target=ExternalOperationTarget(
            kind="external_target",
            value=payload.target,
            destination_class=DestinationClass.UNKNOWN,
        ),
        payload=payload,
        payload_preview=_bounded_preview(
            " | ".join(
                [payload.action, payload.target]
                + [f"{item.name}={item.value}" for item in normalized_fields]
            )
        ),
        payload_digest=compute_payload_digest(payload),
        capability_profile_id=capability_profile_id,
        capability_profile_version=capability_profile_version,
        facts_provenance=OperationFactsProvenance(
            normalizer_id="generic_external",
            normalizer_version=_NORMALIZER_VERSION,
            source_kind="runtime_action",
            source_id=plan_step_id,
        ),
    )


# Replace a proposal payload after HITL while retaining immutable operation identity.
def replace_external_operation_payload(
    proposal: ExternalOperationProposal,
    *,
    payload,
) -> ExternalOperationProposal:
    preview = _payload_preview(payload)
    target = _target_from_payload(payload, fallback=proposal.target)
    effect = _effect_from_payload(payload, fallback=proposal.normalized_effect)
    return replace(
        proposal,
        payload=payload,
        payload_preview=preview,
        payload_digest=compute_payload_digest(payload),
        target=target,
        normalized_effect=effect,
        facts_provenance=replace(
            proposal.facts_provenance,
            source_kind="human_review_edit",
            fact_notes=(*proposal.facts_provenance.fact_notes, "payload_replaced"),
        ),
    )


# Inspect the complete AST so read-looking wrappers cannot hide mutating operations.
def _sql_statement_class(expression: exp.Expression) -> SQLStatementClass:
    mappings: tuple[tuple[type[exp.Expression], SQLStatementClass], ...] = (
        (exp.Insert, SQLStatementClass.INSERT),
        (exp.Update, SQLStatementClass.UPDATE),
        (exp.Delete, SQLStatementClass.DELETE),
        (exp.Create, SQLStatementClass.CREATE),
        (exp.Alter, SQLStatementClass.ALTER),
        (exp.Drop, SQLStatementClass.DROP),
        (exp.Merge, SQLStatementClass.MERGE),
    )
    truncate_type = getattr(exp, "TruncateTable", None)
    for node in expression.walk():
        if isinstance(node, exp.Into):
            return SQLStatementClass.CREATE
        if isinstance(node, exp.Lock):
            return SQLStatementClass.COMMAND
        if truncate_type is not None and isinstance(node, truncate_type):
            return SQLStatementClass.TRUNCATE
        for expression_type, statement_class in mappings:
            if isinstance(node, expression_type):
                return statement_class
    if isinstance(expression, exp.Select):
        return SQLStatementClass.SELECT
    if isinstance(expression, exp.Command):
        return SQLStatementClass.COMMAND
    return SQLStatementClass.UNKNOWN


# Derive the normalized external effect from the parser-owned SQL class.
def _effect_for_statement_class(statement_class: SQLStatementClass) -> ExternalOperationEffect:
    return {
        SQLStatementClass.SELECT: ExternalOperationEffect.READ,
        SQLStatementClass.INSERT: ExternalOperationEffect.CREATE,
        SQLStatementClass.CREATE: ExternalOperationEffect.CREATE,
        SQLStatementClass.UPDATE: ExternalOperationEffect.UPDATE,
        SQLStatementClass.ALTER: ExternalOperationEffect.UPDATE,
        SQLStatementClass.DELETE: ExternalOperationEffect.DELETE,
        SQLStatementClass.DROP: ExternalOperationEffect.DELETE,
        SQLStatementClass.TRUNCATE: ExternalOperationEffect.TRUNCATE,
        SQLStatementClass.MERGE: ExternalOperationEffect.UPDATE,
        SQLStatementClass.COMMAND: ExternalOperationEffect.UNKNOWN,
        SQLStatementClass.MULTIPLE: ExternalOperationEffect.UNKNOWN,
        SQLStatementClass.UNKNOWN: ExternalOperationEffect.UNKNOWN,
    }[statement_class]


# Classify only configured destination facts; semantic content stays judge-owned.
def _classify_destination(
    *,
    recipients: tuple[str, ...],
    prohibited_domains: tuple[str, ...],
) -> DestinationClass:
    if not recipients:
        return DestinationClass.UNKNOWN
    addresses = tuple(
        address
        for raw in recipients
        for address in _parsed_recipient_addresses(raw)
    )
    if not addresses:
        return DestinationClass.UNKNOWN
    domains = tuple(
        address.rsplit("@", 1)[-1]
        for address in addresses
        if "@" in address
    )
    if any(
        address in prohibited_domains or domain in prohibited_domains
        for address, domain in zip(addresses, domains)
    ):
        return DestinationClass.PROHIBITED
    if all(
        domain.endswith(".internal") or domain.endswith(".local")
        for domain in domains
    ):
        return DestinationClass.INTERNAL
    return DestinationClass.EXTERNAL


# Canonicalize display-name and multi-recipient forms before policy sees them.
def _normalize_recipient(value: str | None) -> str | None:
    raw = _optional_single_line(value)
    if raw is None:
        return None
    addresses = _parsed_recipient_addresses(raw)
    return ", ".join(addresses) if addresses else raw


# Parse only syntactically bounded mailbox values and reject ambiguous fragments.
def _parsed_recipient_addresses(value: str) -> tuple[str, ...]:
    parsed = getaddresses([value.replace(";", ",")])
    addresses = tuple(
        address.strip().lower()
        for _, address in parsed
        if address.strip()
        and "@" in address
        and not any(character.isspace() for character in address)
    )
    return addresses if len(addresses) == len(parsed) else ()


# Rebuild target facts from an edited typed payload.
def _target_from_payload(payload, *, fallback: ExternalOperationTarget) -> ExternalOperationTarget:
    if isinstance(payload, CommunicationOperationPayload):
        return ExternalOperationTarget(
            kind="recipient" if payload.recipient else "channel",
            value=payload.recipient or payload.channel,
            destination_class=payload.destination_class,
        )
    if isinstance(payload, GenericExternalOperationPayload):
        return ExternalOperationTarget(
            kind="external_target",
            value=payload.target,
            destination_class=DestinationClass.UNKNOWN,
        )
    return fallback


# Rebuild effect facts when the typed payload makes the operation class explicit.
def _effect_from_payload(payload, *, fallback: ExternalOperationEffect) -> ExternalOperationEffect:
    if isinstance(payload, CommunicationOperationPayload):
        return ExternalOperationEffect.SEND
    if isinstance(payload, DatabaseOperationPayload):
        return _effect_for_statement_class(payload.statement_class)
    return fallback


# Produce a bounded preview for any typed replacement payload.
def _payload_preview(payload) -> str:
    if isinstance(payload, CommunicationOperationPayload):
        return _bounded_preview(
            "\n".join(
                part
                for part in (
                    f"To: {payload.recipient}" if payload.recipient else None,
                    f"Subject: {payload.subject}" if payload.subject else None,
                    payload.body,
                )
                if part
            )
        )
    if isinstance(payload, DatabaseOperationPayload):
        return _bounded_preview(payload.normalized_sql or payload.sql)
    if isinstance(payload, GenericExternalOperationPayload):
        return _bounded_preview(
            " | ".join(
                [payload.action, payload.target]
                + [f"{item.name}={item.value}" for item in payload.fields]
            )
        )
    raise ValueError("Unsupported external operation payload edit.")


# Normalize single-line transport fields without rewriting semantic content.
def _single_line(value: str) -> str:
    return " ".join(str(value).strip().split())


# Normalize optional transport fields.
def _optional_single_line(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _single_line(value)
    return normalized or None


# Bound full bodies while preserving paragraph structure for semantic assessment.
def _bounded_body(value: str) -> str:
    normalized = re.sub(r"\r\n?", "\n", value).strip()
    return normalized[:12000]


# Bound previews separately from the canonical payload used by the digest.
def _bounded_preview(value: str) -> str:
    return value[:4096]


# Resolve whether a database profile is configured as read-only from environment config or conventions.
def _is_database_profile_read_only(profile_id: str) -> bool:
    config_value = os.getenv("LAYER2_DATABASE_PROFILES_CONFIG_PATH")
    if config_value and Path(config_value).exists():
        try:
            payload = json.loads(Path(config_value).read_text(encoding="utf-8"))
            profiles = payload.get("profiles", {})
            if profile_id in profiles:
                return bool(profiles[profile_id].get("read_only", True))
        except Exception:
            pass
    if "read_only" in profile_id or profile_id.endswith(".read_only"):
        return True
    if profile_id.endswith(".write") or "write" in profile_id:
        return False
    return True


__all__ = [
    "normalize_communication_operation",
    "normalize_generic_external_operation",
    "normalize_sql_operation",
    "replace_external_operation_payload",
]
