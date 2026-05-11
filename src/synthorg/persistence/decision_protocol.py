"""Decision records repository protocol."""

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from pydantic import AwareDatetime  # noqa: TC002

from synthorg.core.enums import DecisionOutcome  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT

if TYPE_CHECKING:
    from synthorg.engine.decisions import DecisionRecord

__all__ = [
    "DecisionRepository",
    "DecisionRole",
]


DecisionRole = Literal["executor", "reviewer"]
"""Valid role filters for ``DecisionRepository.list_by_agent``."""


@runtime_checkable
class DecisionRepository(Protocol):
    """Append-only persistence + query interface for ``DecisionRecord``.

    Decision records are immutable audit entries of review gate
    decisions.  No update or delete operations are provided to preserve
    audit integrity.
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
        limit: int = DEFAULT_LIST_LIMIT,
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
        limit: int = DEFAULT_LIST_LIMIT,
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
