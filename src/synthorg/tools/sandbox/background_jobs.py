"""Sandbox-agnostic orchestration over persisted background-job records.

Owns the read/write surface the Docker background-execution mixin,
boot reconciliation, and the terminal tools all need, but knows
nothing about Docker or ``aiodocker`` itself: killing a job's process
is the caller's job (its container-exec mechanism lives beside the
Docker sandbox code, not here), this class only decides WHICH rows
need it and records the outcome.
"""

from collections.abc import Awaitable, Callable

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.sandbox import (
    SANDBOX_BACKGROUND_JOB_REAPED,
    SANDBOX_BACKGROUND_JOB_TIMED_OUT,
    SANDBOX_KILL_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence.background_job_protocol import (
    LIVE_BACKGROUND_JOB_STATUSES,
    BackgroundJobRecord,
    BackgroundJobRepository,
    BackgroundJobStatus,
)

logger = get_logger(__name__)

#: A job past its own ceiling is force-cancelled with this reason
#: recorded nowhere the agent reads (`ORPHANED`/`TIMED_OUT` cover the
#: agent-visible status); logged for the operator instead.
_TIMEOUT_REASON: str = "max_duration_seconds exceeded"


class BackgroundJobRegistry:
    """Read/write surface over :class:`BackgroundJobRepository`.

    Constructed once per Docker sandbox backend and threaded into the
    lifecycle-strategy factory (as the source data for ``pin_check``,
    via the sandbox's own bound method -- this class holds no
    container-exec capability itself), the background-execution mixin,
    boot reconciliation, and the terminal tools' collaborator.
    """

    def __init__(
        self,
        repo: BackgroundJobRepository,
        *,
        clock: Clock | None = None,
    ) -> None:
        """Initialise the registry over *repo*.

        Args:
            repo: Backend-specific background-job repository.
            clock: Clock seam; defaults to :class:`SystemClock`.
        """
        self._repo = repo
        self._clock = clock or SystemClock()

    async def get(self, job_id: NotBlankStr) -> BackgroundJobRecord | None:
        """Read one job's tracking row.

        Returns:
            The persisted record, or ``None`` if no row exists.
        """
        return await self._repo.get(job_id)

    async def save(self, record: BackgroundJobRecord) -> None:
        """Persist *record* (insert or replace)."""
        await self._repo.save(record)

    async def count_live_by_owner(self, owner_id: NotBlankStr) -> int:
        """Count jobs in a live status for one lifecycle owner.

        Returns:
            The number of rows for *owner_id* whose status is in
            :data:`LIVE_BACKGROUND_JOB_STATUSES`.
        """
        return await self._repo.count_live_by_owner(owner_id)

    async def list_by_owner(
        self,
        owner_id: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[BackgroundJobRecord, ...]:
        """List jobs recorded against one lifecycle owner, newest-first.

        Returns:
            Job rows recorded against *owner_id*, newest-first.
        """
        return await self._repo.list_by_owner(owner_id, limit=limit, offset=offset)

    async def list_live_by_container(
        self, container_id: NotBlankStr
    ) -> tuple[BackgroundJobRecord, ...]:
        """List jobs in a live status for one container.

        Unlike :meth:`list_by_container` (every row, any status, used
        by :meth:`reap_for_container`), this is scoped server-side to
        :data:`LIVE_BACKGROUND_JOB_STATUSES` -- the question a pin
        check and a job-limit check both actually ask. Filtering in the
        query rather than in Python matters here: a container reused
        across a long per-agent lifetime can accumulate rows past one
        page, and a Python-side filter over a page fetched by recency
        alone can miss a genuinely live row sitting behind older
        terminal ones, silently unpinning a container a job still runs
        in.

        Returns:
            Live job rows recorded against *container_id*.
        """
        return await self._repo.list_by_container(
            container_id, statuses=LIVE_BACKGROUND_JOB_STATUSES
        )

    async def has_live_jobs(self, container_id: NotBlankStr) -> bool:
        """Answer whether *container_id* currently has any live job.

        A plain, read-only check: unlike :meth:`pin_check`'s own
        ``expire_overdue`` call, this never force-cancels a job past its
        own ``max_duration_seconds`` ceiling. It exists for a cheap
        pre-exec gate (should THIS foreground call be pinned-and-killable
        rather than today's whole-container-stop), not a place to expire
        anything.

        Returns:
            ``True`` while at least one live row exists for the container.
        """
        return bool(await self.list_live_by_container(container_id))

    async def mark_terminal(
        self,
        record: BackgroundJobRecord,
        status: BackgroundJobStatus,
        *,
        exit_code: int | None = None,
    ) -> BackgroundJobRecord:
        """Transition *record* to a new status and persist it.

        The write is conditional on the row *record* was read from
        still being live: poll, cancel, timeout expiry, and
        container-teardown reap can all race to terminalize the SAME
        job, and a blind write would let whichever one runs last
        silently overwrite an earlier, equally valid terminal status
        (e.g. a late CANCELLED clobbering an already-recorded COMPLETED
        and its real exit code). The loser's own attempted status is
        simply discarded in favour of whatever actually landed first.

        Args:
            record: The job row to update.
            status: New status.
            exit_code: Process exit code, when known.

        Returns:
            The updated, persisted record -- or, if another writer had
            already terminalized this job first, that writer's own
            persisted record.
        """
        updated = record.model_copy(
            update={
                "status": status,
                "exit_code": exit_code if exit_code is not None else record.exit_code,
                "updated_at": self._clock.now(),
            }
        )
        applied = await self._repo.save_if_live(updated)
        if applied:
            return updated
        current = await self._repo.get(record.job_id)
        return current if current is not None else updated

    async def expire_overdue(
        self,
        container_id: NotBlankStr,
        *,
        kill_fn: Callable[[NotBlankStr, int], Awaitable[None]],
    ) -> tuple[BackgroundJobRecord, ...]:
        """Force-cancel any live job past its own duration ceiling.

        Self-cleaning: rather than a separate sweep task for
        ``max_duration_seconds``, whatever already asks "is this
        container pinned" (the lifecycle strategy's own grace/idle
        recheck) also answers this, reusing that cadence instead of a
        second polling loop.

        Args:
            container_id: Container to check.
            kill_fn: Async callable killing a job's process group
                inside its container -- the caller's own container-exec
                mechanism; this class has none.

        Returns:
            The jobs that were still live after expiry (i.e. the ones
            NOT force-cancelled by this call).
        """
        live = await self.list_live_by_container(container_id)
        still_live: list[BackgroundJobRecord] = []
        now = self._clock.now()
        for record in live:
            elapsed = (now - record.started_at).total_seconds()
            if elapsed <= record.max_duration_seconds:
                still_live.append(record)
                continue
            if record.pid is not None:
                try:
                    await kill_fn(container_id, record.pid)
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    # The row is still marked TIMED_OUT below regardless:
                    # a failed kill must not also leave the job row live
                    # forever, which would pin this container's grace/idle
                    # teardown off indefinitely (`_await_unpinned`'s own
                    # kill-switch relies on this call converging). The
                    # container's own eventual teardown is the fallback
                    # kill for whatever the exec-level kill missed.
                    logger.warning(
                        SANDBOX_KILL_FAILED,
                        job_id=record.job_id,
                        container_id=container_id[:12],
                        pid=record.pid,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
            await self.mark_terminal(record, BackgroundJobStatus.TIMED_OUT)
            logger.warning(
                SANDBOX_BACKGROUND_JOB_TIMED_OUT,
                job_id=record.job_id,
                container_id=container_id[:12],
                elapsed_seconds=round(elapsed, 1),
                max_duration_seconds=record.max_duration_seconds,
                reason=_TIMEOUT_REASON,
            )
        return tuple(still_live)

    async def reap_for_container(
        self, container_id: NotBlankStr, *, reason: str
    ) -> None:
        """Mark every live job of a destroyed container ``ORPHANED``.

        Wired into every existing container-teardown choke point
        (``_destroy_handle``) plus boot reconciliation: a job whose
        container is gone before it reached a terminal status on its
        own has no process left to poll, cancel, or read output from.

        Args:
            container_id: The container that was (or is about to be)
                torn down.
            reason: Why -- logged, not persisted; the row's own
                ``ORPHANED`` status is the durable signal.
        """
        live = await self.list_live_by_container(container_id)
        for record in live:
            await self.mark_terminal(record, BackgroundJobStatus.ORPHANED)
            logger.info(
                SANDBOX_BACKGROUND_JOB_REAPED,
                job_id=record.job_id,
                container_id=container_id[:12],
                reason=reason,
            )


__all__ = ["BackgroundJobRegistry"]
