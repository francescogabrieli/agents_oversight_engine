# Live transport validation uses ValueError so the HTTP boundary returns a client error.
# ruff: noqa: TRY004
from __future__ import annotations

from enum import Enum
from typing import Any, TypeVar

from layer3_tool_bus_external_capabilities.external_operations.contracts import (
    CommunicationOperationPayload,
    DatabaseOperationParameter,
    DatabaseOperationPayload,
    DestinationClass,
    ExternalOperationPayload,
    ExternalOperationPayloadEdit,
    GenericExternalOperationPayload,
    GenericPayloadField,
    SQLStatementClass,
)

from .review_payload_normalization import (
    normalize_communication_body as _required_communication_body,
    normalize_copy_recipients as _coerce_recipient_tuple,
    normalize_optional_text as _optional_text,
    require_text as _required_text,
)

EnumT = TypeVar("EnumT", bound=Enum)


# Parse one full typed external-operation replacement from its live transport payload.
def coerce_external_operation_edit(payload: Any) -> ExternalOperationPayloadEdit:
    if isinstance(payload, ExternalOperationPayloadEdit):
        return ExternalOperationPayloadEdit(
            payload=_validate_external_operation_payload(payload.payload),
            note=_optional_text(payload.note, field_name="external_operation_edit.note"),
        )
    if not isinstance(payload, dict):
        raise ValueError("external_operation_edit must be an object.")
    typed_payload = payload.get("payload")
    if not isinstance(typed_payload, dict):
        raise ValueError("external_operation_edit is missing its typed payload.")
    return ExternalOperationPayloadEdit(
        payload=_coerce_external_operation_payload(typed_payload),
        note=_optional_text(
            payload.get("note"),
            field_name="external_operation_edit.note",
        ),
    )


# Validate already-typed operation payloads with the same live bounds as JSON payloads.
def _validate_external_operation_payload(payload: Any) -> ExternalOperationPayload:
    if isinstance(payload, CommunicationOperationPayload):
        return CommunicationOperationPayload(
            channel=_required_text(payload.channel, field_name="communication channel"),
            recipient=_optional_text(payload.recipient, field_name="communication recipient"),
            subject=_optional_text(payload.subject, field_name="communication subject"),
            body=_required_communication_body(payload.body),
            destination_class=_coerce_enum(
                DestinationClass,
                payload.destination_class,
                field_name="destination_class",
            ),
            cc=_coerce_recipient_tuple(payload.cc, field_name="cc"),
            bcc=_coerce_recipient_tuple(payload.bcc, field_name="bcc"),
            body_format=payload.body_format,
        )
    if isinstance(payload, DatabaseOperationPayload):
        return _build_database_operation_payload(
            {
                "sql": payload.sql,
                "normalized_sql": payload.normalized_sql,
                "statement_class": payload.statement_class,
                "statement_count": payload.statement_count,
                "parse_valid": payload.parse_valid,
                "ambiguous": payload.ambiguous,
                "database_profile_id": payload.database_profile_id,
                "parameters": [
                    {"name": item.name, "value": item.value}
                    for item in payload.parameters
                ],
                "is_read_only_profile": payload.is_read_only_profile,
            }
        )
    if isinstance(payload, GenericExternalOperationPayload):
        return _build_generic_operation_payload(
            {
                "action": payload.action,
                "target": payload.target,
                "fields": [
                    {"name": item.name, "value": item.value}
                    for item in payload.fields
                ],
            }
        )
    raise ValueError("Unsupported external operation edit payload type.")


# Dispatch a discriminated operation payload through its strict family parser.
def _coerce_external_operation_payload(
    payload: dict[str, Any],
) -> ExternalOperationPayload:
    payload_type = payload.get("payload_type")
    if payload_type == "communication":
        return CommunicationOperationPayload(
            channel=_required_text(
                payload.get("channel"),
                field_name="communication channel",
            ),
            recipient=_optional_text(
                payload.get("recipient"),
                field_name="communication recipient",
            ),
            subject=_optional_text(
                payload.get("subject"),
                field_name="communication subject",
            ),
            body=_required_communication_body(payload.get("body")),
            destination_class=_coerce_enum(
                DestinationClass,
                payload.get("destination_class", DestinationClass.UNKNOWN.value),
                field_name="destination_class",
            ),
            cc=_coerce_recipient_tuple(payload.get("cc"), field_name="cc"),
            bcc=_coerce_recipient_tuple(payload.get("bcc"), field_name="bcc"),
            body_format=payload.get("body_format", "plain"),
        )
    if payload_type == "database":
        return _build_database_operation_payload(payload)
    if payload_type == "generic":
        return _build_generic_operation_payload(payload)
    raise ValueError("Unsupported external_operation_edit payload_type.")


# Build a validated database payload without treating booleans or strings as integers.
def _build_database_operation_payload(
    payload: dict[str, Any],
) -> DatabaseOperationPayload:
    statement_count = payload.get("statement_count", 0)
    if isinstance(statement_count, bool) or not isinstance(statement_count, int):
        raise ValueError("Database statement_count must be an integer.")
    if statement_count < 0:
        raise ValueError("Database statement_count cannot be negative.")
    return DatabaseOperationPayload(
        sql=_required_text(payload.get("sql"), field_name="database sql"),
        normalized_sql=_optional_text(
            payload.get("normalized_sql"),
            field_name="normalized_sql",
        ),
        statement_class=_coerce_enum(
            SQLStatementClass,
            payload.get("statement_class", SQLStatementClass.UNKNOWN.value),
            field_name="statement_class",
        ),
        statement_count=statement_count,
        parse_valid=_required_bool(
            payload.get("parse_valid", False),
            field_name="parse_valid",
        ),
        ambiguous=_required_bool(
            payload.get("ambiguous", True),
            field_name="ambiguous",
        ),
        database_profile_id=_required_text(
            payload.get("database_profile_id", "enterprise_db.read_only"),
            field_name="database_profile_id",
        ),
        parameters=_coerce_database_parameters(payload.get("parameters", [])),
        is_read_only_profile=_required_bool(
            payload.get("is_read_only_profile", True),
            field_name="is_read_only_profile",
        ),
    )


# Parse bounded database parameters without accepting nested transport objects.
def _coerce_database_parameters(
    value: Any,
) -> tuple[DatabaseOperationParameter, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("Database parameters must be an array.")
    parameters: list[DatabaseOperationParameter] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Each database parameter must be an object.")
        parameter_value = item.get("value")
        if parameter_value is not None and not isinstance(
            parameter_value,
            (str, int, float, bool),
        ):
            raise ValueError("Database parameter values must be JSON scalar values.")
        parameters.append(
            DatabaseOperationParameter(
                name=_required_text(
                    item.get("name"),
                    field_name="database parameter name",
                ),
                value=parameter_value,
            )
        )
    return tuple(parameters)


# Build a validated generic payload whose open values remain bounded strings.
def _build_generic_operation_payload(
    payload: dict[str, Any],
) -> GenericExternalOperationPayload:
    raw_fields = payload.get("fields", [])
    if not isinstance(raw_fields, (list, tuple)):
        raise ValueError("Generic operation fields must be an array.")
    fields: list[GenericPayloadField] = []
    for item in raw_fields:
        if not isinstance(item, dict):
            raise ValueError("Each generic operation field must be an object.")
        fields.append(
            GenericPayloadField(
                name=_required_text(
                    item.get("name"),
                    field_name="generic operation field name",
                ),
                value=_required_text(
                    item.get("value"),
                    field_name="generic operation field value",
                ),
            )
        )
    return GenericExternalOperationPayload(
        action=_required_text(payload.get("action"), field_name="generic action"),
        target=_required_text(payload.get("target"), field_name="generic target"),
        fields=tuple(fields),
    )


# Parse a closed enum field from either its typed member or exact string value.
def _coerce_enum(
    enum_type: type[EnumT],
    value: Any,
    *,
    field_name: str,
) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"Unsupported {field_name}: {value}") from exc


# Accept only real booleans for policy-relevant operation facts.
def _required_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean.")
    return value


__all__ = ["coerce_external_operation_edit"]
