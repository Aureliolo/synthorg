"""Background shell job persistence protocol and record.

A ``shell_command`` call backgrounded with ``background=True`` outlives
the tool call that started it: the process keeps running inside the
sandbox container after the turn that spawned it returns. This
repository persists one row per such job so its status survives a
backend restart, so :func:`synthorg.tools.sandbox.reconciliation
.reap_orphaned_background_jobs` can tell a job still running in a live
container from one orphaned by a hard kill, and so the per-owner job
count cap (:meth:`BackgroundJobRepository.count_live_by_owner`) can be
enforced without an in-memory count that a restart would lose.

See ``docs/design/tools.md`` for the wrapper mechanism that starts,
polls, reads and cancels the tracked process itself; this module only
owns the persisted shape.
"""

from enum import StrEnum
from typing import Protocol, override, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository


class BackgroundJobStatus(StrEnum):
    """Status of a backgrounded shell job from the sandbox's perspective."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    ORPHANED = "orphaned"


#: Statuses a job can still be doing work under; used by the per-owner
#: job cap and by every "is this container still pinned" query.
LIVE_BACKGROUND_JOB_STATUSES: frozenset[BackgroundJobStatus] = frozenset(
    {BackgroundJobStatus.PENDING, BackgroundJobStatus.RUNNING}
)


class BackgroundJobRecord(BaseModel):
    """Persisted tracking row for one backgrounded shell job.

    Attributes:
        job_id: Opaque id returned to the agent by ``shell_command``.
        container_id: Docker container id the job's process runs inside.
            Keys the reap-on-teardown hook and the boot reconciliation
            sweep, both of which act on containers, not jobs.
        owner_id: The sandbox lifecycle owner (agent id under per-agent
            strategy, task id under per-task) the job was started under.
            Keys the per-owner job-count cap.
        project_id: The project the job's command ran against, or
            ``None`` when unbound.
        command_repr: Truncated command text, kept for observability
            (``list_background_jobs``) and never the full untruncated
            command.
        pid: Process-group leader PID inside the container, once the
            wrapper confirms the process started. ``None`` while
            ``PENDING``.
        status: Current lifecycle status.
        exit_code: Process exit code, set only once the job has left a
            live status.
        output_path: In-container path of the (write-time-capped)
            captured output file.
        started_at: When the job was accepted.
        updated_at: When ``status`` (or any other field) last changed.
        max_duration_seconds: The ceiling in force when this job
            started; a later operator change to the setting only
            affects jobs started after it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    job_id: NotBlankStr = Field(description="Opaque id returned to the agent")
    container_id: NotBlankStr = Field(description="Docker container id")
    owner_id: NotBlankStr = Field(description="Sandbox lifecycle owner")
    project_id: NotBlankStr | None = Field(default=None, description="Bound project")
    command_repr: NotBlankStr = Field(description="Truncated command text")
    pid: int | None = Field(default=None, gt=0, description="Process-group leader PID")
    status: BackgroundJobStatus = Field(description="Current lifecycle status")
    exit_code: int | None = Field(default=None, description="Process exit code")
    output_path: NotBlankStr = Field(description="In-container captured-output path")
    started_at: AwareDatetime = Field(description="When the job was accepted")
    updated_at: AwareDatetime = Field(description="When the row last changed")
    max_duration_seconds: float = Field(
        gt=0, description="Ceiling in force when this job started"
    )


@runtime_checkable
class BackgroundJobRepository(
    IdKeyedRepository[BackgroundJobRecord, NotBlankStr],
    Protocol,
):
    """Persistence interface for backgrounded shell job records.

    Composes :class:`IdKeyedRepository`: the natural key is ``job_id``.
    ``load_all`` is a bespoke perf method (mirrors
    ``TrackedContainerRepository``) because boot reconciliation reads
    every row at start; ``list_by_container``, ``count_live_by_owner``
    and ``list_by_owner`` are bespoke query methods answering the three
    questions the sandbox and tool layers ask repeatedly and that a
    generic paginated scan would answer too slowly to be usable at
    every grace/idle timer recheck (ADR-0001 D7).
    """

    @override
    async def save(self, entity: BackgroundJobRecord, /) -> None:
        """Insert or replace the tracking row for one job.

        Args:
            entity: Job record to persist.

        Raises:
            QueryError: If the underlying write fails.
        """
        ...

    async def save_if_live(self, entity: BackgroundJobRecord, /) -> bool:
        """Persist *entity* only if the EXISTING row is still live.

        A conditional ``UPDATE ... WHERE job_id = ? AND status IN
        (<live>)``, not an upsert: it never creates a row (every caller
        already holds one fetched from :meth:`get`). Guards every
        terminal-status transition (``mark_terminal``'s four
        independent callers -- poll, cancel, timeout expiry, and
        container-teardown reap) against clobbering a row another
        writer already moved to a DIFFERENT terminal status, which a
        blind :meth:`save` cannot detect.

        Args:
            entity: The job record to persist, with its new status
                already set.

        Returns:
            ``True`` if the write applied (the existing row was still
            live), ``False`` if it did not (another writer already
            transitioned the row to a terminal status first).

        Raises:
            QueryError: If the underlying write fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> BackgroundJobRecord | None:
        """Read the tracking row for one job, or ``None`` if absent.

        Args:
            entity_id: Job id to look up.

        Returns:
            The persisted record, or ``None`` if no row exists.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete the tracking row for one job.

        Args:
            entity_id: Job id to remove.

        Returns:
            ``True`` if a row was deleted, ``False`` if no row existed.

        Raises:
            QueryError: If the underlying DELETE fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[BackgroundJobRecord, ...]:
        """List jobs ordered by ``job_id`` ascending.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Paginated records in ascending job-id order.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...

    async def load_all(self) -> tuple[BackgroundJobRecord, ...]:
        """Load every tracking row in one call (called at boot).

        Reconciliation needs the full set in one round-trip; the table
        is small relative to a paginated scan's overhead for this use.

        Returns:
            All persisted records, order unspecified.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...

    async def list_by_container(
        self,
        container_id: NotBlankStr,
        *,
        statuses: frozenset[BackgroundJobStatus] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[BackgroundJobRecord, ...]:
        """List jobs recorded against one container, newest-first.

        Backs both the pin predicate (any row in
        :data:`LIVE_BACKGROUND_JOB_STATUSES` for this container) and the
        reap-on-teardown hook, which needs every row regardless of
        status to mark none of them left dangling. A container reused
        across many tasks under the per-agent strategy can accumulate
        rows over a long lifetime, so this is paginated like every
        other multi-row query -- which is exactly why the pin predicate
        passes *statuses*: a Python-side filter over one page can miss a
        genuinely live row sitting past the page boundary behind older
        terminal rows for the same container, silently unpinning a
        container a job is still running in. ``None`` (the
        reap-on-teardown hook's own use) returns every status.

        Args:
            container_id: Docker container id to look up.
            statuses: When given, only rows whose status is a member are
                returned (filtered server-side, not fetched-then-Python-
                filtered). ``None`` returns every status.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Job rows recorded against this container, newest-first.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...

    async def count_live_by_owner(self, owner_id: NotBlankStr) -> int:
        """Count jobs in a live status for one lifecycle owner.

        Backs the per-owner job-count cap enforced at job-creation time.

        Args:
            owner_id: Sandbox lifecycle owner to count against.

        Returns:
            The number of rows for this owner whose status is in
            :data:`LIVE_BACKGROUND_JOB_STATUSES`.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...

    async def list_by_owner(
        self,
        owner_id: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[BackgroundJobRecord, ...]:
        """List jobs recorded against one lifecycle owner, newest-first.

        Backs ``list_background_jobs``.

        Args:
            owner_id: Sandbox lifecycle owner to look up.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Job rows recorded against this owner, newest-first.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...


__all__ = [
    "LIVE_BACKGROUND_JOB_STATUSES",
    "BackgroundJobRecord",
    "BackgroundJobRepository",
    "BackgroundJobStatus",
]
