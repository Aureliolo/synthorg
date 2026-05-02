"""Cross-cutting controller response helpers.

Audit ``138-inline-cross-cutting-concerns`` flagged 17+ controller call
sites that hand-rolled the same ``if resource is None: log + raise
NotFoundError`` block.  This module centralises that pattern so the
log-then-raise ordering, the structured kwargs, and the
domain-specific :class:`ErrorCode` selection are owned by one helper
that every controller reuses.
"""

from collections.abc import Mapping  # noqa: TC003 -- runtime annotation
from typing import LiteralString

from synthorg.core.domain_errors import resource_not_found
from synthorg.core.error_taxonomy import ErrorCode
from synthorg.observability import get_logger

logger = get_logger(__name__)


def require_resource_or_404[T](  # noqa: PLR0913 -- intentional rich kwargs surface
    resource: T | None,
    *,
    resource_type: LiteralString,
    identifier: str,
    log_event: LiteralString,
    operation: LiteralString = "read",
    code: ErrorCode = ErrorCode.RESOURCE_NOT_FOUND,
    extra_log_kwargs: Mapping[str, object] | None = None,
) -> T:
    """Return ``resource`` or raise ``NotFoundError`` (logged + RFC 9457).

    The single canonical spelling for the ``if resource is None: log
    then raise`` pattern that recurred across 17+ controller call
    sites (audit ``138-inline-cross-cutting-concerns``).  The helper:

    1. Logs ``log_event`` at WARNING with structured kwargs so the
       miss is observable in the audit trail.
    2. Raises :class:`NotFoundError` via :func:`resource_not_found`
       so the response body carries the **domain-specific**
       ``error_code`` (``code`` argument) rather than the generic
       ``RESOURCE_NOT_FOUND`` -- API clients that want to discriminate
       which resource was missing get the precision back.

    Args:
        resource: The fetched value, or ``None`` when the underlying
            repository / service did not find a record.
        resource_type: Human-readable resource type used in the
            client-visible error message (``"Artifact"``, ``"task"``).
            ``LiteralString`` so the value is known at type-check time
            and cannot be operator-controlled.
        identifier: The missing identifier value the client supplied.
            Quoted into the error message verbatim.
        log_event: Domain-scoped event constant emitted at WARNING.
            ``LiteralString`` so a runtime-derived event name (which
            would defeat the structured-event-name discipline) cannot
            slip through the boundary.
        operation: Audit-log discriminator describing what the caller
            attempted (``"read"`` / ``"update"`` / ``"delete"``).
        code: Resource-specific :class:`ErrorCode` (must be a 3xxx
            ``NOT_FOUND``-band code; :func:`resource_not_found`
            enforces the band).  Defaults to the generic
            ``RESOURCE_NOT_FOUND`` for resources without a dedicated
            code.
        extra_log_kwargs: Additional structured fields to merge into
            the WARNING log (e.g. ``{"reason": "wrong_owner"}``).
            Keys collide-protected: ``id`` / ``operation`` /
            ``resource`` always win to keep the audit-search shape
            stable.

    Returns:
        ``resource`` (narrowed to ``T``) when not ``None``.

    Raises:
        NotFoundError: When ``resource`` is ``None``; the message is
            ``"<resource_type> <identifier!r> not found"`` and the
            instance ``error_code`` matches ``code``.
    """
    if resource is not None:
        return resource
    log_kwargs: dict[str, object] = dict(extra_log_kwargs or {})
    # Stable keys win on collision so audit-search predicates stay
    # consistent across call sites.
    log_kwargs["id"] = identifier
    log_kwargs["operation"] = operation
    log_kwargs["resource"] = resource_type
    logger.warning(log_event, **log_kwargs)
    raise resource_not_found(resource_type, identifier, code=code)


__all__ = ["require_resource_or_404"]
