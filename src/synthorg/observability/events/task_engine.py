"""Task engine event constants."""

from typing import Final

TASK_ENGINE_CREATED: Final[str] = "task_engine.created"
TASK_ENGINE_STARTED: Final[str] = "task_engine.started"
TASK_ENGINE_STOP_REJECTED: Final[str] = "task_engine.stop.rejected"
"""Emitted when ``TaskEngine.stop()`` refuses a call because of an invalid
caller argument (e.g. non-positive ``timeout``). Kept distinct from
``TASK_ENGINE_STOPPED`` so rejected stops do not inflate successful-stop
metrics, and from ``TASK_ENGINE_DRAIN_TIMEOUT`` which is specifically the
hard-deadline / cancellation path."""
TASK_ENGINE_START_REJECTED: Final[str] = "task_engine.start.rejected"
"""Emitted when ``TaskEngine.start()`` refuses to start -- already running,
or unrestartable after a timed-out stop -- and when a ``start()`` attempt
rolls back mid-boot (``reason="startup_rollback"``). Kept distinct from
``TASK_ENGINE_STARTED`` so rejected / rolled-back starts do not inflate
successful-start metrics or alerts."""
TASK_ENGINE_STOPPED: Final[str] = "task_engine.stopped"
TASK_ENGINE_MUTATION_RECEIVED: Final[str] = "task_engine.mutation.received"
TASK_ENGINE_MUTATION_APPLIED: Final[str] = "task_engine.mutation.applied"
TASK_ENGINE_MUTATION_FAILED: Final[str] = "task_engine.mutation.failed"
TASK_ENGINE_SNAPSHOT_PUBLISHED: Final[str] = "task_engine.snapshot.published"
TASK_ENGINE_SNAPSHOT_PUBLISH_FAILED: Final[str] = "task_engine.snapshot.publish_failed"
TASK_ENGINE_QUEUE_FULL: Final[str] = "task_engine.queue.full"
TASK_ENGINE_DRAIN_START: Final[str] = "task_engine.drain.start"
TASK_ENGINE_DRAIN_COMPLETE: Final[str] = "task_engine.drain.complete"
TASK_ENGINE_DRAIN_TIMEOUT: Final[str] = "task_engine.drain.timeout"
TASK_ENGINE_NOT_RUNNING: Final[str] = "task_engine.not_running"
TASK_ENGINE_VERSION_CONFLICT: Final[str] = "task_engine.version.conflict"

TASK_ENGINE_TIMING_FALLBACK: Final[str] = "task_engine.timing.fallback"
"""Emitted when the in-memory ``TaskTimingTracker`` has no creation
timestamp for a task that just transitioned to a terminal state
(typically because the task was created before a process restart).
The emitting site falls back to ``duration_sec=0.0`` for the
``synthorg_task_runs_total`` / ``synthorg_task_duration_seconds``
sample; the WARN keeps the gap searchable so operators can tell
why a task shows up with a zero duration."""
TASK_ENGINE_LOOP_ERROR: Final[str] = "task_engine.loop.error"
TASK_ENGINE_READ_FAILED: Final[str] = "task_engine.read.failed"
TASK_ENGINE_LIST_CAPPED: Final[str] = "task_engine.list.capped"
TASK_ENGINE_FUTURES_FAILED: Final[str] = "task_engine.futures.failed"
TASK_ENGINE_OBSERVER_FAILED: Final[str] = "task_engine.observer.failed"
TASK_ENGINE_OBSERVER_QUEUE_FULL: Final[str] = "task_engine.observer.queue_full"
TASK_ENGINE_OBSERVER_DRAIN_TIMEOUT: Final[str] = "task_engine.observer.drain_timeout"
TASK_ENGINE_LOOP_DIED: Final[str] = "task_engine.loop.died"
TASK_ENGINE_OBSERVER_LOOP_DIED: Final[str] = "task_engine.observer_loop.died"
TASK_ENGINE_TIMEOUT_ENFORCEMENT_SET: Final[str] = "task_engine.timeout_enforcement.set"
"""Emitted when the process-wide ``engine.timeout_enforcement_enabled`` cache
flips. Logged at INFO so an operator can correlate timeout-behaviour changes
with config events; the toggle is a runtime state transition that affects
every engine coroutine entering ``engine_timeout``."""
