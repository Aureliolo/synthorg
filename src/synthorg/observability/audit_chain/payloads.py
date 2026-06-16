"""Typed payload contract for the audit-chain hash-signed log events.

The :class:`AuditChainEventPayload` model gates the payload dict
:class:`~synthorg.observability.audit_chain.sink.AuditChainSink` builds
before signing -- a runtime guard against the field set drifting away
from the fixed iteration in :meth:`AuditChainSink.emit`.

**Byte-stable hashing contract**: the existing serialiser stays
``json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)``
operating on the SAME dict the model was validated against, so the
hash chain is byte-identical across the migration. ``parse_typed`` is
called for validation only and never replaces the dict that goes into
``json.dumps``.
"""

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr


class AuditChainEventPayload(BaseModel):
    """Typed shape of every payload signed and chained by the sink.

    Required fields are populated from the stdlib ``LogRecord``;
    optional fields are merged in only when the corresponding record
    attribute is non-``None`` (preserving the byte-stable serialisation
    that the hash chain depends on).

    Attributes:
        event: Canonical event name (e.g. ``security.auth.login``).
        level: ``LogRecord.levelname`` (``"INFO"``, ``"WARNING"``...).
        timestamp: ``LogRecord.created`` (epoch seconds with subsecond
            precision).
        module: ``LogRecord.module`` -- emitting Python module name.
        tool_name: Optional tool identifier for tool-registry events.
        expected_hash: Optional expected hash (e.g. for signature
            verification audits).
        actual_hash: Optional observed hash (e.g. for tampering
            detection events).
        correlation_id: Optional request correlation ID.
        principal: Optional acting principal identifier.
        resource: Optional resource identifier.
        action_type: Optional action-type taxonomy value.
        error: Optional redacted error description (already routed
            through :func:`safe_error_description` upstream).
        verdict: Optional security/guardrail verdict (e.g. ``allow`` /
            ``deny`` / ``escalate``) so a chained decision event records
            what was decided, not merely that a decision happened.
        model: Optional model identifier for provider-attributed audit
            events, so the chained record carries which model produced
            the audited output.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    event: NotBlankStr
    level: NotBlankStr
    timestamp: float
    module: NotBlankStr
    tool_name: NotBlankStr | None = None
    expected_hash: NotBlankStr | None = None
    actual_hash: NotBlankStr | None = None
    correlation_id: NotBlankStr | None = None
    principal: NotBlankStr | None = None
    resource: NotBlankStr | None = None
    action_type: NotBlankStr | None = None
    error: str | None = None
    verdict: NotBlankStr | None = None
    model: NotBlankStr | None = None


__all__ = ["AuditChainEventPayload"]
