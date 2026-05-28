"""Decision records repository protocol."""

from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.enums import DecisionOutcome
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    AppendOnlyRepository,
)

if TYPE_CHECKING:
    from synthorg.engine.decisions import DecisionRecord

__all__ = [
    "DecisionFilterSpec",
    "DecisionRepository",
    "DecisionRole",
]


DecisionRole = Literal["executor", "reviewer"]
"""Valid role filters for ``DecisionRepository.query``."""


class DecisionFilterSpec(BaseModel):
    """Filter spec for ``DecisionRepository.query`` (ADR-0001)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    task_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by task identifier",
    )
    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by agent identifier",
    )
    role: DecisionRole | None = Field(
        default=None,
        description="Filter by agent role ('executor' or 'reviewer')",
    )


@runtime_checkable
class DecisionRepository(
    AppendOnlyRepository["DecisionRecord", DecisionFilterSpec],
    Protocol,
):
    """Append-only persistence + query interface for ``DecisionRecord``.

    Decision records are immutable audit entries of review gate
    decisions.  No update or delete operations are provided to preserve
    audit integrity.

    Composes :class:`AppendOnlyRepository` (ADR-0001). Bespoke per D7:

    * ``append_with_next_version`` atomically computes version via SQL
      subquery to eliminate the TOCTOU race under concurrent reviewers.
    * ``get(record_id)`` is kept because ``AppendOnlyRepository`` has no
      per-record retrieval (append-only logs are typically queried only
      via filtered scans); callers need direct ID-based lookups.
    * ``list_by_task`` and ``list_by_agent`` are kept because they serve
      different consumers with different sort orders: ``list_by_task``
      returns oldest-first (chronological), while ``list_by_agent``
      returns newest-first (cursor-pagination stable under concurrent
      appends).
    """

    async def append_with_next_version(  # noqa: PLR0913
        self,
        *,
        record_id: NotBlankStr,
        task_id: NotBlankStr,
        approval_id: NotBlankStr | None,
        executing_agent_id: NotBlankStr,
        reviewer_agent_id: NotBlankStr,
        decision: DecisionOutcome,
        reason: str | None,
        criteria_snapshot: tuple[NotBlankStr, ...],
        recorded_at: AwareDatetime,
        metadata: dict[str, object] | None = None,
    ) -> DecisionRecord:
        """Atomically append a decision record computing version in SQL.

        Computes ``version = COALESCE(MAX(version), 0) + 1`` for the
        given ``task_id`` inside a single ``INSERT`` statement (atomic
        under aiosqlite's per-statement serialization), eliminating the
        TOCTOU race that a ``list_by_task`` + ``len(...) + 1`` pattern
        would create under concurrent reviewers.

        Args:
            record_id: Unique record identifier (UUID recommended).
            task_id: Task that was reviewed.
            approval_id: Associated ``ApprovalItem`` identifier, or ``None``.
            executing_agent_id: Agent that performed the work.
            reviewer_agent_id: Agent or human that reviewed.
            decision: Outcome of the review.
            reason: Optional rationale.
            criteria_snapshot: Acceptance criteria at decision time.
            recorded_at: Decision timestamp (must be timezone-aware).
                Normalized to UTC before storage so records read back
                via ``get`` / ``list_by_task`` / ``list_by_agent`` will
                always carry UTC timestamps.
            metadata: Forward-compatible metadata.  Defaults to ``{}``
                when not supplied; callers that do not attach
                metadata do not have to pass an empty dict.

        Returns:
            The persisted ``DecisionRecord`` with the server-assigned
            ``version``.

        Raises:
            DuplicateRecordError: If a record with ``record_id`` already
                exists, or a concurrent writer won the
                ``UNIQUE(task_id, version)`` race.
            QueryError: If the operation fails.
        """
        ...

    async def append(self, event: DecisionRecord) -> None:
        """Append a decision record via precomputed version.

        Normally callers use ``append_with_next_version`` which
        atomically computes the version in SQL. This method is provided
        for completeness of the :class:`AppendOnlyRepository` interface
        when the version is already known (rare).

        Args:
            event: The decision record to persist.

        Raises:
            DuplicateRecordError: If a record with the same ID exists.
            QueryError: If the operation fails.
        """
        ...

    async def query(
        self,
        filter_spec: DecisionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DecisionRecord, ...]:
        """Query decision records with optional filters and pagination.

        Results are ordered by timestamp and ID. When both ``agent_id``
        and ``role`` are specified, returns records where the agent
        acted in that role (``query(DecisionFilterSpec(agent_id="a1",
        role="reviewer"))`` returns decisions reviewed by agent "a1").

        Args:
            filter_spec: Carries optional task_id, agent_id, and role
                filters. All filters are optional and combined with AND.
            limit: Maximum rows to return (>= 1).
            offset: Rows to skip (>= 0).

        Returns:
            Matching decision records as a tuple. When ``task_id`` is
            specified, results are oldest-first (ascending recorded_at).
            When ``agent_id`` / ``role`` are specified without
            ``task_id``, results are newest-first (descending
            recorded_at). Mixed filters default to task-oriented ordering
            (oldest-first).

        Raises:
            QueryError: If the operation fails or pagination args are
                out of range.
        """
        ...

    async def get(self, record_id: NotBlankStr) -> DecisionRecord | None:
        """Retrieve a decision record by ID.

        Args:
            record_id: The record identifier.

        Returns:
            The record, or ``None`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def list_by_task(
        self,
        task_id: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DecisionRecord, ...]:
        """List decision records for a task (paginated, oldest first).

        Args:
            task_id: The task identifier.
            limit: Maximum rows to return (>= 1).
            offset: Rows to skip (>= 0).

        Returns:
            Matching records as a tuple (oldest first).

        Raises:
            QueryError: If the operation fails or pagination args are
                out of range.
        """
        ...

    async def list_by_agent(
        self,
        agent_id: NotBlankStr,
        *,
        role: DecisionRole,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DecisionRecord, ...]:
        """List decision records by agent role (paginated, newest first).

        Args:
            agent_id: The agent identifier.
            role: Either ``"executor"`` or ``"reviewer"``.
            limit: Maximum rows to return (>= 1).
            offset: Rows to skip (>= 0).

        Returns:
            Matching records as a tuple, ordered by
            ``(recorded_at DESC, id DESC)`` so cursor pagination is
            stable under concurrent inserts.

        Raises:
            QueryError: If the operation fails, pagination args are
                out of range, or ``role`` is not a recognised value.
        """
        ...

    async def purge_before(self, threshold: datetime) -> int:
        """Delete decision records older than threshold (retention).

        Args:
            threshold: Records older than this are deleted.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the operation fails.
        """
        ...
