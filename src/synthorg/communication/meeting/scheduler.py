# module-kind: complex_service
"""Meeting scheduler -- background service for periodic and event-triggered meetings.

Bridges meeting configuration and meeting execution by scheduling
frequency-based meetings as periodic asyncio tasks and providing
an API for event-triggered meetings.

One cohesive responsibility: schedule and trigger meetings. The
periodic-task fan-out, event-trigger cooldown enforcement (in-memory
+ durable), per-loop lock rebinding for test isolation, and the
start/stop lifecycle (with shielded drain + unrestartable-after-
timeout policy) share ``_lifecycle_lock`` / ``_cooldown_lock`` /
``_last_triggered`` state; splitting requires extracting that
shared state owner and lock graph, which just relocates complexity.
"""

import asyncio
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from synthorg.communication.meeting.errors import (
    NoParticipantsResolvedError,
    SchedulerAlreadyRunningError,
)
from synthorg.communication.meeting.frequency import frequency_to_seconds
from synthorg.communication.meeting.models import (
    MeetingAgenda,
    MeetingAgendaItem,
    MeetingRecord,
)
from synthorg.communication.meeting.orchestrator import (
    MeetingOrchestrator,
)
from synthorg.communication.meeting.participant import (
    ParticipantResolver,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.meeting import (
    MEETING_EVENT_COOLDOWN_SKIPPED,
    MEETING_EVENT_TRIGGERED,
    MEETING_NO_PARTICIPANTS,
    MEETING_PERIODIC_TRIGGERED,
    MEETING_SCHEDULER_ERROR,
    MEETING_SCHEDULER_STARTED,
    MEETING_SCHEDULER_STOPPED,
    MEETING_SCHEDULER_TASK_DIED,
)

if TYPE_CHECKING:
    # communication.config is a cycle hub (communication.__init__ -> bus ->
    # config reaches back here); MeetingCooldownRepository is a mock-injected
    # protocol whose runtime import would make typeguard reject the fake.
    from synthorg.communication.config import MeetingsConfig
    from synthorg.communication.meeting.config import MeetingTypeConfig
    from synthorg.persistence.meeting_cooldown_protocol import (
        MeetingCooldownRepository,
    )

# Map meeting status values to WS event name strings.
# Mirrors WsEventType.MEETING_* values without importing the API layer.
_STATUS_TO_WS_EVENT: dict[str, str] = {
    "completed": "meeting.completed",
    "failed": "meeting.failed",
    "budget_exhausted": "meeting.failed",
}

logger = get_logger(__name__)

# Minimum participants required for a meeting (leader + at least 1 other).
_MIN_PARTICIPANTS: int = 2

_STOP_DRAIN_TIMEOUT_SECONDS: float = 10.0
"""Hard deadline for the ``stop()`` drain.

Per CLAUDE.md ``## Code Conventions`` > Lifecycle synchronization:
services whose ``stop()`` drains across ``await`` boundaries must
wrap the drain in ``asyncio.wait_for`` so the lifecycle lock cannot
be held indefinitely if a periodic task ignores cancellation.
"""


class MeetingScheduler:
    """Background service for scheduling and triggering meetings.

    Creates periodic asyncio tasks for frequency-based meeting types
    and handles event-triggered meetings on demand.

    Args:
        config: Meetings subsystem configuration.
        orchestrator: Meeting orchestrator for executing meetings.
        participant_resolver: Resolver for participant references.
        event_publisher: Optional callback for publishing WS events
            ``(event_name: str, payload: dict) -> None``.
    """

    __slots__ = (
        "_background_drain_task",
        "_clock",
        "_config",
        "_cooldown_hydrated",
        "_cooldown_lock",
        "_cooldown_lock_loop",
        "_cooldown_repo",
        "_event_publisher",
        "_last_triggered",
        "_lifecycle_lock",
        "_lifecycle_lock_loop",
        "_orchestrator",
        "_resolver",
        "_running",
        "_stop_failed",
        "_tasks",
    )

    def __init__(  # noqa: PLR0913 -- scheduler wiring needs the full dep set
        self,
        *,
        config: MeetingsConfig,
        orchestrator: MeetingOrchestrator,
        participant_resolver: ParticipantResolver,
        event_publisher: Callable[[str, dict[str, object]], None] | None = None,
        clock: Callable[[], float] | None = None,
        cooldown_repo: MeetingCooldownRepository | None = None,
    ) -> None:
        self._config = config
        self._orchestrator = orchestrator
        self._resolver = participant_resolver
        self._event_publisher = event_publisher
        self._clock = clock or time.monotonic
        # When cooldown_repo is supplied the scheduler persists the
        # wall-clock timestamp of every trigger at trigger_event and
        # rehydrates _last_triggered from the repo at start(), so a
        # restart inside a cooldown window cannot let a meeting re-fire.
        # The runtime comparison still uses the monotonic ``_clock`` for
        # performance; hydration translates the persisted wall-clock to
        # a monotonic-equivalent offset via (now_wall - persisted_wall).
        self._cooldown_repo = cooldown_repo
        # Loop-bound asyncio primitives are deferred so the scheduler
        # can be safely re-used across event loops (test scenarios
        # where pytest-asyncio creates a fresh loop per test while a
        # session-scoped Litestar app holds this instance).  The
        # cooldown lock is operational (used by every trigger_event
        # call); the lifecycle lock guards start/stop transitions.
        # Both rebind to the running loop on first access via the
        # ``_lock_for_current_loop`` helper.
        self._cooldown_lock: asyncio.Lock | None = None
        self._cooldown_lock_loop: asyncio.AbstractEventLoop | None = None
        self._lifecycle_lock: asyncio.Lock | None = None
        self._lifecycle_lock_loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task[None]] = []
        # Holds the shielded drain task spawned in stop() so the orphan
        # (timeout case) is observable to tests / operators. Cleared on
        # the next stop() entry, reassigned on completion.
        self._background_drain_task: asyncio.Task[list[BaseException | None]] | None = (
            None
        )
        self._running = False
        # Set to True when a stop() drain exceeds the hard deadline.
        # Prevents a subsequent start() from spawning a second set of
        # periodic tasks on top of orphaned periodic tasks that
        # ignored cancellation. Recovery requires reconstructing the
        # scheduler.
        self._stop_failed = False
        self._last_triggered: dict[str, float] = {}
        # Tracks whether the cooldown dict has been hydrated from the
        # persistent repo this lifetime. Reset on stop so a fresh start
        # re-hydrates from durable state instead of stale in-memory.
        self._cooldown_hydrated = False

    @property
    def running(self) -> bool:
        """Whether the scheduler is currently running."""
        return self._running

    def _lifecycle_lock_for_current_loop(self) -> asyncio.Lock:
        """Return a lifecycle lock bound to the running loop, rebinding if needed.

        Three paths:

        1. No running loop (``RuntimeError`` from ``get_running_loop``):
           build a lock if absent and return it; the caller will
           ``await`` it once a loop is up.
        2. Recorded loop is ``None`` (pre-seeded by a test double
           before any ``start()`` ran): keep the injected lock as-is so
           race tests that pre-acquired it stay deterministic.
        3. Recorded loop is set and differs from ``current`` (a stale
           lock from a previous pytest-asyncio loop): rebind to the
           current running loop so the next ``await`` does not raise
           "loop is closed".
        """
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            if self._lifecycle_lock is None:
                self._lifecycle_lock = asyncio.Lock()
            return self._lifecycle_lock
        if self._lifecycle_lock is None or (
            self._lifecycle_lock_loop is not None
            and self._lifecycle_lock_loop is not current
        ):
            self._lifecycle_lock = asyncio.Lock()
            self._lifecycle_lock_loop = current
        return self._lifecycle_lock

    def _cooldown_lock_for_current_loop(self) -> asyncio.Lock:
        """Return a cooldown lock bound to the running loop, rebinding if needed.

        ``_cooldown_lock`` is operational (acquired by every
        ``trigger_event`` call), so we cannot defer creation to a
        ``start()`` lifecycle hook.  Rebind on each access when the
        loop has changed -- a no-op in production (single loop) and
        the safe path in tests where pytest-asyncio creates a fresh
        loop per test.
        """
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            if self._cooldown_lock is None:
                self._cooldown_lock = asyncio.Lock()
            return self._cooldown_lock
        if self._cooldown_lock is None or self._cooldown_lock_loop is not current:
            self._cooldown_lock = asyncio.Lock()
            self._cooldown_lock_loop = current
        return self._cooldown_lock

    async def _hydrate_cooldowns_from_repo(self) -> None:
        """Load persisted cooldown timestamps into ``_last_triggered``.

        Idempotent within one lifetime: hydrates on the first start()
        call after construction; subsequent re-entries after stop()
        also re-hydrate so durable state takes precedence over any
        stale in-memory survivors.

        Converts persisted wall-clock timestamps to monotonic-equivalent
        offsets: a meeting last fired ``elapsed`` seconds ago in wall
        time is recorded as ``now_monotonic - max(0, elapsed)`` so the
        runtime cooldown comparison (which uses monotonic) still
        respects the persisted interval.
        """
        if self._cooldown_repo is None:
            return
        try:
            records = await self._cooldown_repo.load_all()
        except Exception as exc:
            reraise_critical(exc)
            logger.error(
                MEETING_SCHEDULER_ERROR,
                phase="hydrate_cooldown_repo",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # Abort startup rather than continuing with an empty
            # ``_last_triggered``: a silent hydrate failure drops the
            # persisted cooldown floor, so an event-triggered meeting
            # could fire again immediately after a restart. The operator
            # opted into durable cooldowns by configuring the repo, so a
            # load failure must surface, not be swallowed.
            raise
        from datetime import UTC, datetime  # noqa: PLC0415

        now_wall = datetime.now(UTC)
        now_monotonic = self._clock()
        # Acquire the cooldown lock so a trigger_event that races the
        # post-start hydrate cannot interleave its read-then-write
        # against the bulk hydrate write.
        async with self._cooldown_lock_for_current_loop():
            # Replace, do not merge: a re-entry after stop() must let
            # durable state fully supersede in-memory survivors, so a
            # meeting type absent from the persisted set drops its stale
            # in-memory cooldown rather than retaining it.
            self._last_triggered.clear()
            for record in records:
                elapsed = (now_wall - record.last_triggered_at).total_seconds()
                self._last_triggered[record.meeting_type_name] = now_monotonic - max(
                    0.0, elapsed
                )
            self._cooldown_hydrated = True

    async def _persist_cooldown(self, meeting_type_name: str) -> None:
        """Upsert the wall-clock timestamp for one meeting type's cooldown.

        Called inside ``trigger_event`` after the in-memory dict has
        been updated. A persistence failure logs at WARNING and then
        re-raises so the durable cooldown row is never silently lost:
        the caller surfaces the failure rather than continuing with an
        in-memory-only cooldown that vanishes on restart.
        """
        if self._cooldown_repo is None:
            return
        from datetime import UTC, datetime  # noqa: PLC0415

        from synthorg.core.types import NotBlankStr  # noqa: PLC0415
        from synthorg.persistence.meeting_cooldown_protocol import (  # noqa: PLC0415
            MeetingCooldownRecord,
        )

        record = MeetingCooldownRecord(
            meeting_type_name=NotBlankStr(meeting_type_name),
            last_triggered_at=datetime.now(UTC),
        )
        try:
            await self._cooldown_repo.save(record)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                MEETING_SCHEDULER_ERROR,
                phase="persist_cooldown",
                meeting_type=meeting_type_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    async def start(self) -> None:
        """Start periodic tasks for all frequency-based meeting types.

        No-op if ``config.enabled`` is False.

        Holds ``_lifecycle_lock`` across the full body so the check-and-set
        on ``_running`` and the per-type task-spawn loop are atomic
        against concurrent ``start()`` / ``stop()`` calls.

        Raises:
            SchedulerAlreadyRunningError: If the scheduler is already running.
        """
        async with self._lifecycle_lock_for_current_loop():
            # If every recorded task is bound to a dead loop or already
            # done, the scheduler instance survived a loop teardown
            # (test scenarios where pytest-asyncio creates a fresh loop
            # per test). Treat the carried-over state as gone so this
            # ``start()`` rebuilds tasks on the live loop instead of
            # raising ``SchedulerAlreadyRunningError`` against ghosts.
            current_loop = asyncio.get_running_loop()
            if self._tasks and all(
                task.done() or task.get_loop() is not current_loop
                for task in self._tasks
            ):
                self._tasks = []
                self._running = False

            if self._stop_failed:
                logger.warning(
                    MEETING_SCHEDULER_ERROR,
                    reason="unrestartable_after_timeout",
                )
                msg = (
                    "Meeting scheduler is unrestartable after a timed-out "
                    "stop; construct a fresh MeetingScheduler instead"
                )
                raise SchedulerAlreadyRunningError(msg)
            if self._running:
                logger.warning(
                    MEETING_SCHEDULER_ERROR,
                    reason="already_running",
                )
                msg = "Meeting scheduler is already running"
                raise SchedulerAlreadyRunningError(msg)

            if not self._config.enabled:
                logger.info(
                    MEETING_SCHEDULER_STARTED,
                    enabled=False,
                )
                return

            await self._hydrate_cooldowns_from_repo()
            self._running = True

            scheduled = self.get_scheduled_types()
            # Transactional task-spawn loop: if any task creation or
            # callback registration raises partway through the loop,
            # cancel and drain every task already spawned, reset
            # ``_tasks`` + ``_running``, and re-raise the original
            # exception. Without this rollback an exception on meeting
            # type N would leave periodic tasks for types 0..N-1 alive
            # while ``start()`` reports failure, so the caller sees a
            # stopped scheduler that is silently still firing.
            spawned_tasks: list[asyncio.Task[None]] = []
            try:
                for mt in scheduled:
                    task = asyncio.create_task(
                        self._run_periodic(mt),
                        name=f"meeting-{mt.name}",
                    )
                    task.add_done_callback(
                        log_task_exceptions(
                            logger,
                            MEETING_SCHEDULER_TASK_DIED,
                            meeting_type=mt.name,
                        ),
                    )
                    spawned_tasks.append(task)
            except BaseException:
                for task in spawned_tasks:
                    task.cancel()
                if spawned_tasks:
                    await asyncio.gather(*spawned_tasks, return_exceptions=True)
                self._tasks = []
                self._running = False
                raise
            self._tasks = spawned_tasks

            logger.info(
                MEETING_SCHEDULER_STARTED,
                periodic_count=len(scheduled),
                triggered_count=len(self.get_triggered_types()),
            )

    def _handle_drain_results(
        self,
        results: list[BaseException | None],
    ) -> None:
        """Surface drain failures from cancelled periodic tasks.

        Cancelled results are expected; ``MemoryError`` and
        ``RecursionError`` propagate so a system-critical death is not
        hidden behind a clean-shutdown log line; other exceptions are
        logged at ERROR with a redacted description.
        """
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                continue
            if isinstance(result, MemoryError | RecursionError):
                raise result
            if isinstance(result, Exception):
                log_exception_redacted(
                    logger,
                    MEETING_SCHEDULER_ERROR,
                    result,
                    note="periodic task error during shutdown",
                )

    def _drop_cross_loop_tasks(self, current_loop: asyncio.AbstractEventLoop) -> bool:
        """Discard tasks bound to a dead loop.

        Returns:
            ``True`` when every recorded task is bound to a loop other
            than *current_loop* (or ``self._tasks`` is empty), so a
            ``stop()`` call has nothing to cancel/drain here and should
            short-circuit; ``False`` otherwise.
        """
        if not self._tasks:
            return False
        if all(task.get_loop() is not current_loop for task in self._tasks):
            self._tasks = []
            self._running = False
            return True
        return False

    async def stop(self) -> None:
        """Cancel all periodic tasks and wait for completion.

        Holds ``_lifecycle_lock`` so ``stop()`` cannot race a
        partially-constructed ``start()``.

        Raises:
            TimeoutError: If the drain exceeds the hard stop deadline;
                the scheduler is marked unrestartable.
        """
        async with self._lifecycle_lock_for_current_loop():
            if not self._running:
                return

            current_loop = asyncio.get_running_loop()
            if self._drop_cross_loop_tasks(current_loop):
                logger.info(MEETING_SCHEDULER_STOPPED)
                return

            for task in self._tasks:
                task.cancel()
            if self._tasks:
                results: list[BaseException | None]

                # Spawn the drain as a separate task and ``shield`` it
                # from the outer ``wait_for`` cancellation: if a
                # periodic task suppresses ``CancelledError`` and
                # continues working, ``wait_for(gather(...))`` would
                # block INSIDE the lifecycle lock waiting for the
                # suppressed cancellation to take effect -- the
                # "hard deadline" would be soft. With ``shield``, the
                # outer ``wait_for`` times out the *wait* only; the
                # shielded drain continues running in the background
                # but does not prevent ``stop()`` from exiting and
                # releasing ``_lifecycle_lock``.
                async def _drain() -> list[BaseException | None]:
                    """Gather the cancelled periodic tasks, capturing errors.

                    Returns:
                        Each task's result or its exception (gathered with
                        ``return_exceptions=True``).
                    """
                    return await asyncio.gather(
                        *self._tasks,
                        return_exceptions=True,
                    )

                drain_task: asyncio.Task[list[BaseException | None]] = (
                    asyncio.create_task(_drain())
                )
                # Park the reference so tests can assert "drain finished"
                # / "drain orphaned after stop_failed" without grepping
                # the asyncio task registry. Cleared at the head of the
                # next stop() invocation.
                self._background_drain_task = drain_task
                try:
                    results = await asyncio.wait_for(
                        asyncio.shield(drain_task),
                        timeout=_STOP_DRAIN_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    # Hard deadline hit. Mark unrestartable and
                    # re-raise: a future start() must not spawn a
                    # second set of periodic tasks alongside the
                    # orphaned drain_task (which may still be waiting
                    # on cancellation-suppressing periodic tasks).
                    # Leave ``_tasks`` + ``_running`` intact; caller
                    # receives TimeoutError and must reconstruct a
                    # fresh ``MeetingScheduler``.
                    self._stop_failed = True
                    # TRY400: logger.exception here would append a
                    # TimeoutError traceback with no actionable diagnostic
                    # information beyond the structured fields below.
                    logger.error(
                        MEETING_SCHEDULER_ERROR,
                        note=(
                            "stop exceeded hard deadline; "
                            "scheduler marked unrestartable"
                        ),
                        timeout_seconds=_STOP_DRAIN_TIMEOUT_SECONDS,
                        pending_tasks=sum(1 for t in self._tasks if not t.done()),
                    )
                    raise
                self._handle_drain_results(results)
            self._tasks = []
            self._running = False

            logger.info(MEETING_SCHEDULER_STOPPED)

    async def trigger_event(
        self,
        event_name: str,
        *,
        context: Mapping[str, object] | None = None,
    ) -> tuple[MeetingRecord, ...]:
        """Trigger all meeting types matching the given event name.

        Args:
            event_name: Event trigger value to match against.
            context: Optional context passed to participant resolver
                and agenda builder.

        Returns:
            Tuple of meeting records for all triggered meetings
            (empty if no matching types).
        """
        if not self._config.enabled:
            return ()

        matching = tuple(mt for mt in self._config.types if mt.trigger == event_name)
        if not matching:
            return ()

        # Serialize cooldown check + time recording to prevent
        # concurrent trigger_event() calls from both bypassing cooldown.
        async with self._cooldown_lock_for_current_loop():
            now = self._clock()
            eligible: list[MeetingTypeConfig] = []
            for mt in matching:
                if mt.min_interval_seconds is not None:
                    last = self._last_triggered.get(mt.name)
                    if last is not None and (now - last) < mt.min_interval_seconds:
                        logger.info(
                            MEETING_EVENT_COOLDOWN_SKIPPED,
                            meeting_type=mt.name,
                            event_name=event_name,
                            elapsed_seconds=now - last,
                            min_interval_seconds=mt.min_interval_seconds,
                        )
                        continue
                eligible.append(mt)

            if not eligible:
                return ()

            logger.info(
                MEETING_EVENT_TRIGGERED,
                event_name=event_name,
                matching_count=len(eligible),
            )

            for mt in eligible:
                # Persist first: if the durable write fails the loop
                # aborts (via re-raise) before the in-memory cooldown is
                # set, so we never record a cooldown that vanishes on
                # restart and lets the meeting re-fire.
                await self._persist_cooldown(mt.name)
                self._last_triggered[mt.name] = now

        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(self._execute_meeting(mt, context)) for mt in eligible
            ]

        return tuple(r for t in tasks if (r := t.result()) is not None)

    def get_scheduled_types(self) -> tuple[MeetingTypeConfig, ...]:
        """Return all frequency-based meeting type configs.

        Returns:
            Tuple of meeting types with a frequency set.
        """
        return tuple(mt for mt in self._config.types if mt.frequency is not None)

    def get_triggered_types(self) -> tuple[MeetingTypeConfig, ...]:
        """Return all trigger-based meeting type configs.

        Returns:
            Tuple of meeting types with a trigger set.
        """
        return tuple(mt for mt in self._config.types if mt.trigger is not None)

    async def _run_periodic(
        self,
        meeting_type: MeetingTypeConfig,
    ) -> None:
        """Infinite loop: sleep for the interval, then execute the meeting.

        Catches ``CancelledError`` to exit cleanly on stop.
        Catches ``Exception`` inside the loop body so transient
        errors do not kill the periodic task.

        Args:
            meeting_type: The meeting type configuration.

        Raises:
            TypeError: If ``meeting_type`` has no frequency (not a
                scheduled type).
            asyncio.CancelledError: Propagated on stop so the periodic
                task exits cleanly.
        """
        if meeting_type.frequency is None:
            msg = (
                f"_run_periodic called with non-scheduled "
                f"meeting type {meeting_type.name!r}"
            )
            raise TypeError(msg)
        interval = frequency_to_seconds(meeting_type.frequency)

        # Sleep-first: avoids duplicate meetings on restart/deploy.
        try:
            # lint-allow: long-running-loop-kill-switch -- stop() cancel.
            while True:
                await asyncio.sleep(interval)
                logger.info(
                    MEETING_PERIODIC_TRIGGERED,
                    meeting_type=meeting_type.name,
                    interval_seconds=interval,
                )
                try:
                    await self._execute_meeting(meeting_type)
                except Exception as exc:
                    reraise_critical(exc)
                    logger.warning(
                        MEETING_SCHEDULER_ERROR,
                        meeting_type=meeting_type.name,
                        note="periodic execution failed",
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
        except asyncio.CancelledError:  # noqa: TRY203
            raise

    async def _execute_meeting(
        self,
        meeting_type: MeetingTypeConfig,
        context: Mapping[str, object] | None = None,
    ) -> MeetingRecord | None:
        """Resolve participants, build agenda, and delegate to orchestrator.

        Handles errors gracefully: logs and returns None on failure.

        Args:
            meeting_type: The meeting type configuration.
            context: Optional context for participant resolution
                and agenda building.

        Returns:
            Meeting record on success, None if skipped or on error.
        """
        resolved = await self._resolve_participants(meeting_type, context)
        if resolved is None:
            return None

        # First resolved participant is designated as the meeting leader.
        leader_id = resolved[0]
        participant_ids = resolved[1:]

        try:
            agenda = self._build_default_agenda(meeting_type, context)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                MEETING_SCHEDULER_ERROR,
                meeting_type=meeting_type.name,
                note="agenda build failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

        return await self._run_and_publish(
            meeting_type,
            leader_id,
            participant_ids,
            agenda,
        )

    async def _resolve_participants(
        self,
        meeting_type: MeetingTypeConfig,
        context: Mapping[str, object] | None,
    ) -> tuple[str, ...] | None:
        """Resolve and validate participants for a meeting.

        Args:
            meeting_type: The meeting type configuration.
            context: Optional event context.

        Returns:
            Resolved participant tuple, or None on failure.
        """
        try:
            resolved = await self._resolver.resolve(
                meeting_type.participants,
                context,
            )
        except NoParticipantsResolvedError:
            logger.warning(
                MEETING_NO_PARTICIPANTS,
                meeting_type=meeting_type.name,
            )
            return None
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                MEETING_SCHEDULER_ERROR,
                meeting_type=meeting_type.name,
                note="participant resolution failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

        if len(resolved) < _MIN_PARTICIPANTS:
            logger.warning(
                MEETING_NO_PARTICIPANTS,
                meeting_type=meeting_type.name,
                resolved_count=len(resolved),
                min_required=_MIN_PARTICIPANTS,
            )
            return None

        return resolved

    async def _run_and_publish(
        self,
        meeting_type: MeetingTypeConfig,
        leader_id: str,
        participant_ids: tuple[str, ...],
        agenda: MeetingAgenda,
    ) -> MeetingRecord | None:
        """Invoke orchestrator and publish event on success.

        Args:
            meeting_type: The meeting type configuration.
            leader_id: ID of the meeting leader.
            participant_ids: IDs of remaining participants.
            agenda: The meeting agenda.

        Returns:
            Meeting record on success, None on error.
        """
        self._publish_started_event(meeting_type.name)
        try:
            record = await self._orchestrator.run_meeting(
                meeting_type_name=meeting_type.name,
                protocol_config=meeting_type.protocol_config,
                agenda=agenda,
                leader_id=leader_id,
                participant_ids=tuple(participant_ids),
                token_budget=meeting_type.duration_tokens,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                MEETING_SCHEDULER_ERROR,
                meeting_type=meeting_type.name,
                note="orchestrator execution failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

        self._publish_meeting_event(record)
        return record

    def _publish_meeting_event(self, record: MeetingRecord) -> None:
        """Publish a WebSocket event for a meeting result.

        Best-effort: publish errors are logged and swallowed.

        Args:
            record: The completed meeting record.
        """
        if self._event_publisher is None:
            return
        event_name = _STATUS_TO_WS_EVENT.get(record.status.value)
        if event_name is None:
            return
        try:
            self._event_publisher(
                event_name,
                {
                    "meeting_id": record.meeting_id,
                    "meeting_type": record.meeting_type_name,
                    "status": record.status.value,
                },
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                MEETING_SCHEDULER_ERROR,
                meeting_id=record.meeting_id,
                meeting_type=record.meeting_type_name,
                note="event publisher failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    def _publish_started_event(self, meeting_type_name: str) -> None:
        """Publish a meeting.started WS event before execution begins.

        Best-effort: publish errors are logged and swallowed.
        """
        if self._event_publisher is None:
            return
        try:
            self._event_publisher(
                "meeting.started",
                {
                    "meeting_type": meeting_type_name,
                    "status": "in_progress",
                },
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                MEETING_SCHEDULER_ERROR,
                meeting_type=meeting_type_name,
                note="started event publish failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    @staticmethod
    def _build_default_agenda(
        meeting_type: MeetingTypeConfig,
        context: Mapping[str, object] | None,
    ) -> MeetingAgenda:
        """Create a default agenda from meeting type name and context.

        Args:
            meeting_type: The meeting type configuration.
            context: Optional context dict -- keys become agenda items.

        Returns:
            A meeting agenda with title and optional context items.
        """
        ctx = context or {}
        items = tuple(
            MeetingAgendaItem(title=str(k), description=_format_ctx_value(v))
            for k, v in ctx.items()
        )
        return MeetingAgenda(
            title=meeting_type.name,
            context=", ".join(f"{k}: {_format_ctx_value(v)}" for k, v in ctx.items()),
            items=items,
        )


def _format_ctx_value(value: object) -> str:
    """Format a context value as a human-readable string.

    Lists/tuples/sets are comma-joined; scalars use ``str()``.
    Strings and bytes are not treated as iterables.

    Returns:
        The human-readable string form of the context value.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(str(item) for item in value)
    return str(value)
