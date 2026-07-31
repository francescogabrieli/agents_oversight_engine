from __future__ import annotations

import hashlib
import os
import smtplib
import socket
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import getaddresses
from typing import Any

from mcp.server.fastmcp import FastMCP

from .ledger import ServerIdempotencyLedger


# Keep the SMTP defensive bound aligned with communication.email.send.input.v2.
_MAX_EMAIL_BODY_CHARS = 50_000


# Build the communication MCP server with one bounded email capability.
def build_email_server(
    *,
    ledger: ServerIdempotencyLedger | None = None,
) -> FastMCP:
    resolved_ledger = ledger or ServerIdempotencyLedger()
    server = FastMCP("governed-communication")

    # Expose only the bounded typed email operation.
    @server.tool(name="send_email")
    def send_email(
        recipient: str,
        subject: str,
        body: str,
        idempotency_key: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send one configured SMTP email after typed registry authorization."""
        return _send_email(
            recipient=recipient,
            subject=subject,
            body=body,
            cc=tuple(cc or ()),
            bcc=tuple(bcc or ()),
            idempotency_key=idempotency_key,
            ledger=resolved_ledger,
        )

    return server


# Execute one SMTP delivery without logging credentials or message content.
def _send_email(
    *,
    recipient: str,
    subject: str,
    body: str,
    cc: tuple[str, ...],
    bcc: tuple[str, ...],
    idempotency_key: str,
    ledger: ServerIdempotencyLedger,
) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    settings_error = _validate_email_settings()
    if settings_error is not None:
        return _response(
            status="rejected",
            summary=settings_error,
            error_code="smtp_configuration_incomplete",
            retryable=False,
            timestamp=started,
        )
    recipients = _validated_recipients((recipient, *cc, *bcc))
    if not recipients:
        return _response(
            status="rejected",
            summary="No syntactically valid configured recipient was provided.",
            error_code="recipient_invalid",
            retryable=False,
            timestamp=started,
        )
    if len(recipients) > 20 or len(body) > _MAX_EMAIL_BODY_CHARS:
        return _response(
            status="rejected",
            summary="Email payload exceeds the configured defensive bounds.",
            error_code="payload_bounds_exceeded",
            retryable=False,
            timestamp=started,
        )

    reservation = ledger.reserve(
        idempotency_key=idempotency_key,
        capability_id="communication.email.send",
    )
    if reservation.prior_response is not None:
        return reservation.prior_response
    if reservation.prior_in_progress:
        return _response(
            status="indeterminate",
            summary="A prior delivery attempt is unresolved and was not repeated.",
            error_code="prior_attempt_unresolved",
            retryable=False,
            timestamp=started,
        )

    sender = os.environ["LAYER2_SMTP_SENDER"].strip()
    message_id = _stable_message_id(idempotency_key=idempotency_key, sender=sender)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message.set_content(body)
    try:
        refused = _smtp_send(message=message, envelope_recipients=recipients)
        if refused:
            response = _response(
                status="indeterminate",
                summary=(
                    "The SMTP provider accepted an unknown subset of recipients; "
                    "automatic retry is unsafe."
                ),
                error_code="smtp_partial_recipient_acceptance",
                retryable=False,
                timestamp=started,
                external_reference=message_id,
                metadata={"rejected_recipient_count": len(refused)},
            )
        else:
            response = _response(
                status="accepted",
                summary="The SMTP provider accepted the message for delivery.",
                error_code=None,
                retryable=False,
                timestamp=started,
                external_reference=message_id,
                metadata={"provider": "smtp"},
            )
    except (socket.timeout, TimeoutError, smtplib.SMTPServerDisconnected):
        response = _response(
            status="indeterminate",
            summary="The SMTP connection ended without a reliable delivery outcome.",
            error_code="smtp_outcome_indeterminate",
            retryable=False,
            timestamp=started,
            external_reference=message_id,
        )
    except (
        smtplib.SMTPConnectError,
        smtplib.SMTPAuthenticationError,
        smtplib.SMTPRecipientsRefused,
    ) as exc:
        response = _response(
            status="failed",
            summary=f"SMTP delivery was rejected deterministically ({type(exc).__name__}).",
            error_code="smtp_delivery_failed",
            retryable=isinstance(exc, smtplib.SMTPConnectError),
            timestamp=started,
            external_reference=message_id,
        )
    except (smtplib.SMTPException, OSError) as exc:
        response = _response(
            status="indeterminate",
            summary=f"SMTP delivery ended without reliable confirmation ({type(exc).__name__}).",
            error_code="smtp_outcome_indeterminate",
            retryable=False,
            timestamp=started,
            external_reference=message_id,
        )
    ledger.complete(idempotency_key=idempotency_key, response=response)
    return response


# Open the configured SMTP transport and return only rejected envelope recipients.
def _smtp_send(
    *,
    message: EmailMessage,
    envelope_recipients: tuple[str, ...],
) -> dict[str, tuple[int, bytes]]:
    host = os.environ["LAYER2_SMTP_HOST"].strip()
    port = int(os.getenv("LAYER2_SMTP_PORT", "587"))
    timeout = float(os.getenv("LAYER2_SMTP_TIMEOUT_SECONDS", "15"))
    use_ssl = _env_bool("LAYER2_SMTP_USE_SSL")
    use_tls = _env_bool("LAYER2_SMTP_USE_TLS")
    client_type = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with client_type(host=host, port=port, timeout=timeout) as client:
        if use_tls and not use_ssl:
            client.starttls()
        username = os.getenv("LAYER2_SMTP_USERNAME")
        password = os.getenv("LAYER2_SMTP_PASSWORD")
        if username or password:
            if not username or not password:
                raise smtplib.SMTPException("SMTP authentication configuration is incomplete.")
            client.login(username, password)
        return client.send_message(message, to_addrs=list(envelope_recipients))


# Validate required adapter configuration before reserving the idempotency key.
def _validate_email_settings() -> str | None:
    if not os.getenv("LAYER2_SMTP_HOST") or not os.getenv("LAYER2_SMTP_SENDER"):
        return "SMTP host and sender identity must be configured explicitly."
    return None


# Normalize only valid mailbox addresses from the bounded typed fields.
def _validated_recipients(values: tuple[str, ...]) -> tuple[str, ...]:
    parsed = getaddresses([value for value in values if value])
    addresses = tuple(
        address.strip().lower()
        for _, address in parsed
        if address.strip() and "@" in address and not any(char.isspace() for char in address)
    )
    return addresses if len(addresses) == len(parsed) else ()


# Derive a stable RFC-style message id without exposing the idempotency key.
def _stable_message_id(*, idempotency_key: str, sender: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]
    domain = sender.rsplit("@", 1)[-1] if "@" in sender else "localhost"
    return f"<{digest}@{domain}>"


# Build the closed audit-safe response shape consumed by the external executor.
def _response(
    *,
    status: str,
    summary: str,
    error_code: str | None,
    retryable: bool,
    timestamp: datetime,
    external_reference: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "external_execution.tool_response.v1",
        "status": status,
        "external_reference": external_reference,
        "provider": "smtp",
        "timestamp": timestamp.isoformat(),
        "summary": summary,
        "error_code": error_code,
        "retryable": retryable,
        "metadata": metadata or {},
    }


# Parse one opt-in boolean environment setting.
def _env_bool(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["build_email_server"]
