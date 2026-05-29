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

from datetime import datetime
from typing import Final, Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, AppendOnlyRepository
from synthorg.providers.management.capability_dtos import (
    ProviderAuditEvent,
)

_DEFAULT_LIST_LIMIT_50: Final[int] = 50


class ProviderAuditFilterSpec(BaseModel):
    """Filter spec for ``ProviderAuditRepo.query`` (ADR-0001).

    Attributes:
        provider_name: Provider whose events to read. Always required;
            the audit log has no global enumeration.
        after_id: Keyset cursor. ``None`` starts at the latest event;
            any other value reads strictly older events than the
            supplied id.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    provider_name: NotBlankStr = Field(description="Provider whose events to read")
    after_id: int | None = Field(
        default=None,
        description="Keyset cursor; rows with id < after_id are returned",
    )


@runtime_checkable
class ProviderAuditRepo(
    AppendOnlyRepository[ProviderAuditEvent, ProviderAuditFilterSpec],
    Protocol,
):
    """Append-only persistence + query interface for provider mutation audit.

    Composes :class:`AppendOnlyRepository` (ADR-0001) plus two bespoke
    methods retained under ADR D7:

    * ``list`` carries keyset-pagination + ``has_more`` semantics that
      drive the dashboard's audit drawer. The generic ``query`` is
      offset-based and does not surface ``has_more``; the keyset shape
      is genuinely cheaper for large logs.
    * ``purge_before_id`` deletes by integer id (a primary-key range
      scan) rather than the generic ``purge_before(threshold)`` which
      goes through the ``occurred_at`` index; both are kept so the
      retention sweeper can pick the cheapest variant for its
      configured cutoff.

    Implementations live under ``persistence/sqlite/`` and
    ``persistence/postgres/`` with a shared dual-backend conformance
    suite under ``tests/integration/persistence/``.
    """

    @override
    async def append(self, event: ProviderAuditEvent) -> None:
        """Persist one audit event (append-only).

        The ``id`` field on the input event is ignored; the persistence
        layer assigns the next monotonic id internally. Callers that
        need the assigned id should use :meth:`record` instead.

        Args:
            event: The event to persist. ``id`` is ignored on input.

        Raises:
            QueryError: If the underlying write fails.
        """
        ...

    async def record(self, event: ProviderAuditEvent) -> ProviderAuditEvent:
        """Append and return the saved event with its assigned id.

        Bespoke per ADR-0001 D7: the dashboard surfaces the
        repo-assigned id immediately on the create response so a
        follow-up audit-drawer fetch can target the new row without an
        extra round-trip. The generic ``append`` returns ``None``.

        Args:
            event: The event to persist. ``id`` is ignored on input.

        Returns:
            The saved event with the repo-assigned ``id`` populated.

        Raises:
            QueryError: If the underlying write fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: ProviderAuditFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ProviderAuditEvent, ...]:
        """Offset-paginated read; newest-first, no ``has_more`` signal.

        Args:
            filter_spec: Carries ``provider_name`` and ``after_id``.
            limit: Maximum events to return.
            offset: Rows to skip before returning ``limit`` rows.

        Returns:
            Events newest-first, paginated.

        Raises:
            QueryError: If the underlying read fails.
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

        Bespoke per ADR-0001 D7. Reads ``WHERE provider_name = ? AND
        (after_id IS NULL OR id < after_id) ORDER BY id DESC LIMIT ? +
        1``. The trailing ``+ 1`` row is the overflow detector for
        ``has_more``; the caller must slice to ``limit`` before
        returning to clients.

        Args:
            provider_name: Provider whose events to read. Always
                required; the audit log has no global enumeration.
            after_id: Keyset cursor. ``None`` starts at the latest
                event; any other value reads strictly older events
                than ``after_id``.
            limit: Page size (1-200, clamped by the controller).

        Returns:
            Tuple of (events, has_more). ``events`` has at most
            ``limit`` rows, ordered by ``id`` descending. ``has_more``
            is True iff a follow-up page exists.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...

    @override
    async def purge_before(self, threshold: datetime) -> int:
        """Delete events older than ``threshold`` by occurred_at.

        Generic surface. Slower than :meth:`purge_before_id` when the
        retention sweeper can express its cutoff as an id, but kept
        so the protocol satisfies :class:`AppendOnlyRepository`.

        Args:
            threshold: UTC timestamp; rows with ``occurred_at <
                threshold`` are removed.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the DELETE fails.
        """
        ...

    async def purge_before_id(self, *, before_id: int) -> int:
        """Delete events with ``id < before_id`` (retention sweeper).

        Bespoke per ADR-0001 D7: id-based purge is a primary-key range
        scan and is materially faster than the generic
        :meth:`purge_before`. The operator-configurable retention
        sweeper uses whichever variant matches the cutoff it has
        already resolved.

        Args:
            before_id: Rows with ``id < before_id`` are removed.
                ``id == before_id`` is kept.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the DELETE fails.
        """
        ...
