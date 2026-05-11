"""SSRF violation service layer.

Thin wrapper over :class:`SsrfViolationRepository` so callers do not
reach into ``app_state.persistence.ssrf_violations`` directly.  Owns
the audit trail for violation recording and resolution -- the
underlying repository is intentionally silent on mutations (per the
persistence boundary rule: repositories must not log mutations
themselves).

Resolution is a security-sensitive event: the WHO + WHEN of an
operator allowing or denying a previously-blocked URL is captured at
this layer via :data:`SECURITY_SSRF_VIOLATION_ALLOWED` /
:data:`SECURITY_SSRF_VIOLATION_DENIED` so the entries land on the
signed audit chain alongside other security mutations.  Failed
resolution attempts emit
:data:`SECURITY_SSRF_VIOLATION_RESOLUTION_FAILED` rather than the
success verb so SIEM readers can distinguish a failed resolution
from an actual allow / deny decision.  Read-side fetch / list events
stay on the API namespace because they carry no audit-chain
implication.
"""

from typing import TYPE_CHECKING, Final

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_SSRF_VIOLATION_FETCH_FAILED,
    API_SSRF_VIOLATION_LISTED,
)
from synthorg.observability.events.security import (
    SECURITY_SSRF_VIOLATION_ALLOWED,
    SECURITY_SSRF_VIOLATION_DENIED,
    SECURITY_SSRF_VIOLATION_RECORDED,
    SECURITY_SSRF_VIOLATION_RESOLUTION_FAILED,
)
from synthorg.security.ssrf_violation import (
    SsrfViolation,
    SsrfViolationStatus,
)

if TYPE_CHECKING:
    from pydantic import AwareDatetime

    from synthorg.persistence.ssrf_violation_protocol import SsrfViolationRepository

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 100


class SsrfViolationService:
    """Wraps :class:`SsrfViolationRepository` with uniform audit logging.

    Errors from the underlying repository
    (``DuplicateRecordError``, ``QueryError``, ``ValueError`` from
    invalid status transitions) propagate unchanged so the controller
    can map them to the appropriate HTTP response.

    Args:
        repo: SSRF violation repository implementation.
    """

    __slots__ = ("_repo",)

    _repo: SsrfViolationRepository

    def __init__(self, *, repo: SsrfViolationRepository) -> None:
        self._repo = repo

    async def record(self, violation: SsrfViolation) -> None:
        """Persist a freshly-recorded violation and audit the event.

        Args:
            violation: The violation to persist.  Typically constructed
                in :data:`SsrfViolationStatus.PENDING` state by the
                self-healing security flow; the model validator allows
                pre-resolved rows for migrations or imports.

        Raises:
            DuplicateRecordError: A violation with the same id already
                exists (logged at WARNING before propagating).
            QueryError: Repository write failure (logged at WARNING
                before propagating).
        """
        try:
            await self._repo.save(violation)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                SECURITY_SSRF_VIOLATION_RECORDED,
                violation_id=violation.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            SECURITY_SSRF_VIOLATION_RECORDED,
            violation_id=violation.id,
            hostname=violation.hostname,
            port=violation.port,
            provider_name=violation.provider_name,
            status=violation.status.value,
        )

    async def get(
        self,
        violation_id: NotBlankStr,
    ) -> SsrfViolation | None:
        """Fetch a violation by id.

        Args:
            violation_id: Identifier of the violation to fetch.

        Returns:
            The violation, or ``None`` when no row matches.

        Raises:
            QueryError: Repository read failure (logged at WARNING
                before propagating).
        """
        try:
            return await self._repo.get(violation_id)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            # Single-violation fetch failures get their own event so
            # endpoint-specific alerting can distinguish them from
            # list-level failures (``API_SSRF_VIOLATION_LISTED``).
            logger.warning(
                API_SSRF_VIOLATION_FETCH_FAILED,
                violation_id=violation_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    async def list_violations(
        self,
        *,
        status: SsrfViolationStatus | None = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> tuple[SsrfViolation, ...]:
        """List violations with an optional ``status`` filter.

        Emits :data:`API_SSRF_VIOLATION_LISTED` at DEBUG with the
        result count for traceability.

        Args:
            status: Filter by status (``None`` for all).
            limit: Maximum number of results (must be positive).

        Returns:
            Tuple of matching violations, ordered by timestamp DESC.

        Raises:
            ValueError: If *limit* is not positive.
            QueryError: Repository read failure (logged at WARNING
                before propagating).
        """
        try:
            rows = await self._repo.list_violations(status=status, limit=limit)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                API_SSRF_VIOLATION_LISTED,
                status_filter=status.value if status is not None else None,
                limit=limit,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.debug(
            API_SSRF_VIOLATION_LISTED,
            count=len(rows),
            status_filter=status.value if status is not None else None,
        )
        return rows

    async def update_status(
        self,
        violation_id: NotBlankStr,
        *,
        status: SsrfViolationStatus,
        resolved_by: NotBlankStr,
        resolved_at: AwareDatetime,
    ) -> bool:
        """Transition a pending violation to ALLOWED or DENIED.

        Audit-critical: emits one of the security audit-chain events
        (:data:`SECURITY_SSRF_VIOLATION_ALLOWED` or
        :data:`SECURITY_SSRF_VIOLATION_DENIED`) with the resolver
        identity and resolution timestamp on success.  Skipped when
        the row was missing or already resolved -- in those cases the
        repository returns ``False`` and no audit fires.

        Args:
            violation_id: Identifier of the violation to update.
            status: New status (must NOT be
                :data:`SsrfViolationStatus.PENDING`).
            resolved_by: Operator (or system principal) resolving the
                violation.
            resolved_at: Timestamp of the resolution decision.

        Returns:
            ``True`` if a pending violation was found and transitioned,
            ``False`` if the violation was not found or already
            resolved.

        Raises:
            ValueError: If *status* is :data:`SsrfViolationStatus.PENDING`.
            QueryError: Repository write failure (logged at WARNING
                before propagating).
        """
        # Branch the resolution event on the new status so the audit
        # chain carries a distinct verb (allowed vs denied) rather than
        # forcing readers to introspect the payload.
        success_event = (
            SECURITY_SSRF_VIOLATION_ALLOWED
            if status is SsrfViolationStatus.ALLOWED
            else SECURITY_SSRF_VIOLATION_DENIED
        )
        try:
            updated = await self._repo.update_status(
                violation_id,
                status=status,
                resolved_by=resolved_by,
                resolved_at=resolved_at,
            )
        except MemoryError, RecursionError:
            raise
        except ValueError as exc:
            # Invalid status transition (e.g. PENDING) is a caller bug
            # and a security-relevant audit signal: log under the
            # dedicated failure event (NOT the success allowed/denied
            # verb) so SIEM filters can distinguish a failed resolution
            # from an actual decision.
            logger.warning(
                SECURITY_SSRF_VIOLATION_RESOLUTION_FAILED,
                violation_id=violation_id,
                status=status.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        except Exception as exc:
            logger.warning(
                SECURITY_SSRF_VIOLATION_RESOLUTION_FAILED,
                violation_id=violation_id,
                status=status.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        if updated:
            logger.info(
                success_event,
                violation_id=violation_id,
                status=status.value,
                resolved_by=resolved_by,
                resolved_at=resolved_at.isoformat(),
            )
        return updated
