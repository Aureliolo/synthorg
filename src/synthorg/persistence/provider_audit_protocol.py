"""Persistence protocol for the Provider mutation audit log.

The provider audit log is **append-only**: every mutation against a
``ProviderConfig`` (create, update, delete, model add/remove/config,
credential rotate, rate-limit edit, preset override edit, bulk model
sync) writes one row through :class:`ProviderAuditService.record`.
The audit-log read endpoint
(``GET /api/v1/providers/{name}/audit``) drives the UI's audit drawer.

Pagination is keyset-based on the integer ``id`` column, which is
both the primary key and a natural monotonic sort key (autoincrement
in SQLite, ``BIGSERIAL`` in Postgres).  The cursor encodes the last
``id`` returned; the next page reads ``WHERE id < after_id ORDER BY
id DESC LIMIT ?``.

This protocol is distinct from :class:`AuditRepository` (security-eval
audit) which lives in ``audit_protocol.py``.  Security audit captures
*what an agent tried to do*; provider audit captures *what an
operator changed about provider config*.  They share the spirit
(append-only, queryable, retention-bounded) but not the schema or
write path.
"""

from typing import Final, Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.providers.management.capability_dtos import (
    ProviderAuditEvent,  # noqa: TC001
)

_DEFAULT_LIST_LIMIT_50: Final[int] = 50


@runtime_checkable
class ProviderAuditRepo(Protocol):
    """Append-only persistence + query interface for provider mutation audit.

    Implementations live under ``persistence/sqlite/`` and
    ``persistence/postgres/`` with a shared dual-backend conformance
    suite under ``tests/integration/persistence/``.
    """

    async def record(self, event: ProviderAuditEvent) -> ProviderAuditEvent:
        """Persist one audit event (append-only).

        The ``id`` field on the input event is ignored; the persistence
        layer assigns the next monotonic id and returns the saved
        event with ``id`` populated.

        Args:
            event: The event to persist.  ``id`` is ignored on input.

        Returns:
            The saved event with the repo-assigned ``id`` populated.

        Raises:
            QueryError: If the underlying write fails.
        """
        ...

    async def list(
        self,
        *,
        provider_name: NotBlankStr,
        after_id: int | None = None,
        limit: int = _DEFAULT_LIST_LIMIT_50,
    ) -> tuple[tuple[ProviderAuditEvent, ...], bool]:
        """List events for one provider, newest first, with keyset paging.

        Reads ``WHERE provider_name = ? AND (after_id IS NULL OR id <
        after_id) ORDER BY id DESC LIMIT ? + 1``.  The trailing ``+ 1``
        row is the overflow detector for ``has_more``; the caller must
        slice to ``limit`` before returning to clients.

        Args:
            provider_name: Provider whose events to read.  This is
                always required -- the audit log has no global "list
                everything" query.
            after_id: Keyset cursor.  ``None`` starts at the latest
                event; any other value reads strictly older events
                than ``after_id``.
            limit: Page size (1-200, clamped by the controller).

        Returns:
            Tuple of (events, has_more).  ``events`` has at most
            ``limit`` rows, ordered by ``id`` descending.  ``has_more``
            is True iff a follow-up page exists.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...

    async def purge_before_id(self, *, before_id: int) -> int:
        """Delete events older than ``before_id`` (retention sweeper).

        Mirrors the ``AuditRepository.purge_before`` exception to the
        append-only rule: the operator-configurable retention window
        is enforced by deleting old rows; this is a per-row mutation
        API only in support of that sweeper, not a general delete
        operation.

        Args:
            before_id: Rows with ``id < before_id`` are removed.
                ``id == before_id`` is kept.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the DELETE fails.
        """
        ...
