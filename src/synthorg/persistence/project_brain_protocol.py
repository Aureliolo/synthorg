"""Long-horizon project-brain persistence protocol.

Append-only store of :class:`BrainEntry` revisions. A change to a logical entry
is a new row (same ``entry_id``, ``revision`` incremented), never an in-place
update. The current state of the brain is the projection of the latest revision
per ``entry_id``; the full revision chain is the history.

The body bytes also live in the project git workspace as a versioned snapshot;
this protocol is the authoritative structured store used for queries.
"""

from datetime import datetime
from typing import Protocol, override, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    AppendOnlyRepository,
)
from synthorg.project_brain.models import (
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
)

BrainEntryRevisionKey = tuple[NotBlankStr, NotBlankStr, int]
"""Composite ``(project_id, entry_id, revision)`` key for an exact revision."""


class BrainFilterSpec(BaseModel):
    """Filter spec for brain queries.

    ``project_id`` is required: the brain is always project-scoped, so
    cross-project listing is intentionally absent. The remaining fields narrow
    the result and are combined with AND.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project")
    entry_kind: BrainEntryKind | None = Field(
        default=None,
        description="Optional kind filter",
    )
    status: BrainEntryStatus | None = Field(
        default=None,
        description="Optional status filter",
    )
    tag: NotBlankStr | None = Field(
        default=None,
        description="Optional tag filter (single tag, exact match)",
    )
    author: NotBlankStr | None = Field(
        default=None,
        description="Optional author filter",
    )
    related_task_id: NotBlankStr | None = Field(
        default=None,
        description="Optional filter: entries referencing this task id",
    )
    updated_since: AwareDatetime | None = Field(
        default=None,
        description="Only entries with recorded_at >= this timestamp",
    )


@runtime_checkable
class ProjectBrainRepository(
    AppendOnlyRepository[BrainEntry, BrainFilterSpec],
    Protocol,
):
    """Append-only persistence and query interface for :class:`BrainEntry`.

    Composes :class:`AppendOnlyRepository` (every change is an append). Bespoke
    methods are added under
    `ADR-0001 <docs/decisions/0001-repository-protocol-consolidation.md>`_ D7
    because they encode a domain invariant the generic surface cannot express:
    monotonic per-entry revisioning and the current-state projection.

    Ordering invariants:

    * :meth:`query` and :meth:`append` follow the append-only contract: every
      stored revision row, newest-first.
    * :meth:`list_current` and :meth:`get_current` return only the latest
      revision per ``entry_id``.
    * :meth:`history` returns one entry's revision chain oldest-first.
    """

    async def append_with_next_revision(self, entry: BrainEntry) -> BrainEntry:
        """Append ``entry`` at the next revision for its ``entry_id``.

        Computes ``revision = COALESCE(MAX(revision), 0) + 1`` partitioned by
        ``entry_id`` inside a single ``INSERT`` (atomic under the backend's
        per-statement serialisation), eliminating the time-of-check-to-
        time-of-use race that a read-then-write pattern would create under
        concurrent writers. ``UNIQUE(entry_id, revision)`` is the backstop.

        The ``revision`` field on the supplied ``entry`` is ignored on input;
        the returned entry carries the server-assigned revision.

        Args:
            entry: The entry to append; ``entry_id`` identifies the logical
                record (a fresh id creates revision 1, an existing id appends
                the next revision).

        Returns:
            The persisted :class:`BrainEntry` with the assigned ``revision``.

        Raises:
            BrainEntryRevisionConflictError: If a concurrent writer won the
                ``UNIQUE(entry_id, revision)`` race.
            QueryError: If the database operation fails.
        """
        ...

    async def get_current(
        self,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
    ) -> BrainEntry | None:
        """Return the latest revision of one entry, or ``None`` if absent.

        Args:
            project_id: Owning project.
            entry_id: Logical entry id.

        Returns:
            The latest :class:`BrainEntry` revision, or ``None``.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    async def list_current(
        self,
        filter_spec: BrainFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[BrainEntry, ...]:
        """Return the current-state projection (latest revision per entry).

        Selects the top revision per ``entry_id`` via
        ``ROW_NUMBER() OVER (PARTITION BY entry_id ORDER BY revision DESC) = 1``
        then applies the filter. Window functions behave identically on SQLite
        (3.25 and later) and Postgres. Results are newest-first by
        ``recorded_at``.

        Args:
            filter_spec: Filter dimensions; ``project_id`` is required.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Current-state entries matching the filter.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    async def history(
        self,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[BrainEntry, ...]:
        """Return the full revision chain of one entry, oldest-first.

        Args:
            project_id: Owning project.
            entry_id: Logical entry id.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            The entry's revisions, oldest-first.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    async def get(self, entity_id: BrainEntryRevisionKey) -> BrainEntry | None:
        """Return one exact revision, or ``None`` if absent.

        Args:
            entity_id: Composite ``(project_id, entry_id, revision)`` key.

        Returns:
            The matching :class:`BrainEntry`, or ``None``.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    async def count(self, filter_spec: BrainFilterSpec) -> int:
        """Count current-state entries matching the filter.

        Counts the current-state projection (one row per ``entry_id``), so the
        REST and dashboard list views can paginate without over-counting
        superseded revisions.

        Args:
            filter_spec: Filter dimensions; ``project_id`` is required.

        Returns:
            Number of current-state entries that match.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    @override
    async def append(self, event: BrainEntry) -> None:
        """Append a brain entry revision with a precomputed revision.

        Normally callers use :meth:`append_with_next_revision`, which assigns
        the revision atomically in SQL. This method is provided for
        completeness of the :class:`AppendOnlyRepository` interface when the
        revision is already known (rare).

        Args:
            event: The entry revision to persist.

        Raises:
            BrainEntryRevisionConflictError: If ``(entry_id, revision)`` exists.
            QueryError: If the database operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: BrainFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[BrainEntry, ...]:
        """Return matching entry revisions (all revisions), newest-first.

        Unlike :meth:`list_current`, this returns every stored revision row
        that matches the filter, not just the current one. Useful for audit
        scans and re-index replay.

        Args:
            filter_spec: Filter dimensions; ``project_id`` is required.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Matching revisions ordered newest-first by ``recorded_at``.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    @override
    async def purge_before(self, threshold: datetime) -> int:
        """Purge superseded historical revisions older than ``threshold``.

        Guarded retention: this purges only non-current revisions (those that
        are not the latest revision of their ``entry_id``) recorded before
        ``threshold``. The latest revision of every entry is always retained so
        current state is never destroyed by a retention sweep.

        Args:
            threshold: Superseded revisions older than this are deleted.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the database operation fails.
        """
        ...
