"""Service that owns provider mutation audit writes + reads.

Keeps the audit logic out of :class:`ProviderManagementService` so the
mutation entry points stay small.  The service is the canonical
point that **every** provider mutation funnels through; controllers
never call the repo directly.

Payload contents are constructed by the caller and must mask
credential fragments before reaching here (``"prefix***last4"``).  The
service does not re-validate; it is the caller's contract to keep
audit rows secret-free.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from synthorg.observability import get_logger
from synthorg.providers.management.capability_dtos import (
    ProviderAuditActor,
    ProviderAuditEvent,
    ProviderAuditEventType,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.persistence.provider_audit_protocol import ProviderAuditRepo

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


class ProviderAuditService:
    """Service-layer wrapper over :class:`ProviderAuditRepo`.

    Args:
        repo: A ``ProviderAuditRepo`` implementation.
    """

    def __init__(self, repo: ProviderAuditRepo) -> None:
        self._repo = repo

    async def record(
        self,
        *,
        provider_name: NotBlankStr,
        event_type: ProviderAuditEventType,
        actor: ProviderAuditActor,
        payload: dict[str, Any] | None = None,
    ) -> ProviderAuditEvent:
        """Write one audit row.

        Args:
            provider_name: Provider the mutation targets.
            event_type: Mutation category.
            actor: Who performed the mutation.
            payload: Event-specific metadata (must be credential-masked).

        Returns:
            The saved event with the repo-assigned ``id`` populated.
        """
        event = ProviderAuditEvent(
            provider_name=provider_name,
            event_type=event_type,
            actor=actor,
            payload=payload or {},
            occurred_at=datetime.now(UTC),
        )
        return await self._repo.record(event)

    async def list_for_provider(
        self,
        *,
        provider_name: NotBlankStr,
        after_id: int | None = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> tuple[tuple[ProviderAuditEvent, ...], bool]:
        """Read one provider's audit log, newest first, with ``has_more``.

        Returns:
            A ``(events, has_more)`` tuple: the page of audit events
            (newest first) and whether more rows remain.
        """
        return await self._repo.list(
            provider_name=provider_name,
            after_id=after_id,
            limit=limit,
        )

    async def purge_before_id(self, *, before_id: int) -> int:
        """Retention sweeper: drop rows older than ``before_id``.

        Returns:
            The number of audit rows deleted.
        """
        return await self._repo.purge_before_id(before_id=before_id)
