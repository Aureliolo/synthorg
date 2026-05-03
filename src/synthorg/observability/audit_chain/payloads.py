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
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    event: str
    level: str
    timestamp: float
    module: str
    tool_name: str | None = None
    expected_hash: str | None = None
    actual_hash: str | None = None
    correlation_id: str | None = None
    principal: str | None = None
    resource: str | None = None
    action_type: str | None = None
    error: str | None = None


__all__ = ["AuditChainEventPayload"]
