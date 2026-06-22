"""Version + timing tracking for TaskEngine bookkeeping.

Wraps two plain dicts -- per-task version counters and per-task
creation timestamps -- with seed, bump, check, and remove operations.
Extracted from ``task_engine.py`` to keep the main module focused on
lifecycle and queue management.
"""

from datetime import (
    datetime,
)

from opentelemetry.trace import Span, Tracer

from synthorg.engine.errors import TaskVersionConflictError
from synthorg.observability import get_logger
from synthorg.observability.events.task_engine import TASK_ENGINE_VERSION_CONFLICT
from synthorg.observability.tracing.instrumentation import get_tracer

logger = get_logger(__name__)


class VersionTracker:
    """In-memory per-task version counter for optimistic concurrency.

    After a restart the tracker is empty.  The first time an unknown
    task is encountered during a ``check()`` call, it is seeded at
    version 1 -- a heuristic baseline, **not** loaded from persistence.
    This makes subsequent optimistic-concurrency checks work within the
    current engine lifetime but cannot detect conflicts that span
    restarts.

    **Limitation:** version tracking is volatile -- it resets on process
    restart.  After a restart, the first ``expected_version=1`` check
    for any task will pass even if the task was mutated many times in a
    prior lifetime.  Durable version tracking (persisted alongside the
    task) is a future enhancement.

    This class is designed for single-writer access from the
    ``TaskEngine`` processing loop and is **not** thread-safe.
    """

    def __init__(self) -> None:
        self._versions: dict[str, int] = {}

    def seed(self, task_id: str) -> None:
        """Ensure *task_id* has a baseline version (idempotent)."""
        if task_id not in self._versions:
            self._versions[task_id] = 1

    def set_initial(self, task_id: str, version: int) -> None:
        """Set *task_id* to *version* unconditionally (used on create).

        Raises:
            ValueError: If *version* is less than 1.
        """
        if version < 1:
            msg = f"Version must be >= 1, got {version}"
            raise ValueError(msg)
        self._versions[task_id] = version

    def bump(self, task_id: str) -> int:
        """Increment and return the version counter for *task_id*.

        If *task_id* is not yet tracked, it is seeded at version 1
        first, so the returned value will be 2 (not 1).

        Returns:
            The post-increment version (``>= 2`` for a newly-tracked
            task, otherwise ``previous + 1``).
        """
        self.seed(task_id)
        version = self._versions[task_id] + 1
        self._versions[task_id] = version
        return version

    def get(self, task_id: str) -> int:
        """Return the current version (0 if not tracked)."""
        return self._versions.get(task_id, 0)

    def remove(self, task_id: str) -> None:
        """Remove version tracking for a deleted task."""
        self._versions.pop(task_id, None)

    def check(
        self,
        task_id: str,
        expected_version: int | None,
    ) -> None:
        """Raise ``TaskVersionConflictError`` if versions disagree.

        Seeds the version at 1 if the task is not yet tracked so that
        optimistic concurrency works within the current engine lifetime.

        Raises:
            TaskVersionConflictError: When ``expected_version`` does
                not match the tracker's current version for
                ``task_id``.
        """
        if expected_version is None:
            return
        self.seed(task_id)
        current = self._versions[task_id]
        if current != expected_version:
            msg = (
                f"Version conflict for task {task_id!r}: "
                f"expected {expected_version}, current {current}"
            )
            logger.warning(
                TASK_ENGINE_VERSION_CONFLICT,
                task_id=task_id,
                expected_version=expected_version,
                current_version=current,
            )
            raise TaskVersionConflictError(msg)


class TaskTimingTracker:
    """In-memory per-task creation timestamps for duration metrics.

    Mirrors :class:`VersionTracker` semantics: the engine seeds an
    entry on ``apply_create`` and reads it on ``apply_transition`` /
    ``apply_cancel`` to compute duration for
    ``synthorg_task_runs_total`` and ``synthorg_task_duration_seconds``.

    **Immutability exemption (CLAUDE.md ``# lint-allow: immutability``):**
    the underlying dict is mutated in place rather than wrapped in
    ``MappingProxyType``. The tracker is volatile single-writer state
    owned by the TaskEngine processing loop; it is never exposed to
    callers and resets on every process restart, so the read-only
    enforcement that ``MappingProxyType`` exists to provide is moot.
    Read accessors (:meth:`get_creation`) return immutable
    ``datetime`` values, not internal references, so callers cannot
    mutate the dict via the public API.

    **Volatility limitation:** like version tracking, timing state
    resets on process restart. A task created before the restart
    that transitions to a terminal state after the restart will have
    no recorded creation time; the engine emits a duration of 0.0
    plus a WARN log so the gap is searchable. Persisting creation
    timestamps alongside the task is a future enhancement that
    requires a schema migration.

    Single-writer, not thread-safe.
    """

    def __init__(self) -> None:
        self._created_at: dict[str, datetime] = {}

    def record_creation(self, task_id: str, created_at: datetime) -> None:
        """Stamp *task_id* with its creation time (overwrites).

        Args:
            task_id: Task identifier.
            created_at: Creation timestamp; must be timezone-aware
                and in UTC. Naive datetimes (or anything other than
                UTC) raise ``ValueError`` to prevent silent metric
                corruption from a caller that forgot ``tzinfo=UTC``.

        Raises:
            ValueError: If *created_at* is naive or not in UTC.
        """
        offset = (
            created_at.tzinfo.utcoffset(created_at)
            if created_at.tzinfo is not None
            else None
        )
        if offset is None or offset.total_seconds() != 0:
            msg = (
                f"TaskTimingTracker.record_creation requires a UTC datetime;"
                f" got {created_at!r} (tzinfo={created_at.tzinfo!r})"
            )
            raise ValueError(msg)
        self._created_at[task_id] = created_at

    def get_creation(self, task_id: str) -> datetime | None:
        """Return the recorded creation time, or ``None`` if absent."""
        return self._created_at.get(task_id)

    def remove(self, task_id: str) -> None:
        """Drop the creation timestamp for a deleted task."""
        self._created_at.pop(task_id, None)


class TaskSpanTracker:
    """In-memory per-task ``task.run`` OTel spans for the task lifetime.

    Mirrors :class:`TaskTimingTracker`: the engine opens a ``task.run``
    span on ``apply_create`` (kept open across mutation calls -- it is
    deliberately NOT a ``with``-scoped current span, since create and the
    terminal transition arrive on separate processing-loop turns) and
    ends it on the truly-terminal transition / cancellation. With tracing
    disabled, :func:`get_tracer` yields a ``NoOpTracer`` so every operation
    here is effectively free.

    **Child-nesting limitation:** the ``agent.execution`` span runs in a
    worker on a separate turn (and, in distributed mode, a separate
    process), so it does not auto-nest under this span. Parenting it would
    require propagating the ``task.run`` span context through the work
    queue (W3C ``traceparent`` on the queue message); this span therefore
    stands as a self-contained task-lifetime span keyed by ``task.id``.

    **Volatility:** like the sibling trackers this is volatile
    single-writer state owned by the TaskEngine loop; a task whose
    terminal transition lands after a process restart has no open span to
    close (the span was abandoned with the prior process). Single-writer,
    not thread-safe.
    """

    def __init__(self, *, tracer: Tracer | None = None) -> None:
        self._spans: dict[str, Span] = {}
        self._tracer = tracer

    def start(self, task_id: str, *, task_type: str) -> None:
        """Open a ``task.run`` span for *task_id* (overwrites any prior).

        Args:
            task_id: Task identifier (also set as the ``task.id`` attribute).
            task_type: Task-type label set as ``task.type``.
        """
        tracer = self._tracer or get_tracer()
        span = tracer.start_span("task.run")
        span.set_attribute("task.id", task_id)
        span.set_attribute("task.type", task_type)
        self._spans[task_id] = span

    def end(self, task_id: str, *, final_status: str) -> None:
        """Stamp the final status and end *task_id*'s span (idempotent).

        Args:
            task_id: Task identifier.
            final_status: Terminal status value set as ``task.status.final``.
        """
        span = self._spans.pop(task_id, None)
        if span is None:
            return
        span.set_attribute("task.status.final", final_status)
        span.end()

    def remove(self, task_id: str) -> None:
        """End and drop a deleted task's span without a terminal status."""
        span = self._spans.pop(task_id, None)
        if span is not None:
            span.end()
