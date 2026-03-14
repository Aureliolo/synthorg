"""Meeting scheduler — background service for periodic and event-triggered meetings.

Bridges meeting configuration and meeting execution by scheduling
frequency-based meetings as periodic asyncio tasks and providing
an API for event-triggered meetings.
"""

import asyncio
from collections.abc import Callable  # noqa: TC003
from typing import TYPE_CHECKING, Any

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
    MeetingOrchestrator,  # noqa: TC001
)
from synthorg.communication.meeting.participant import (
    ParticipantResolver,  # noqa: TC001
)
from synthorg.observability import get_logger
from synthorg.observability.events.meeting import (
    MEETING_EVENT_TRIGGERED,
    MEETING_NO_PARTICIPANTS,
    MEETING_PERIODIC_TRIGGERED,
    MEETING_SCHEDULER_ERROR,
    MEETING_SCHEDULER_STARTED,
    MEETING_SCHEDULER_STOPPED,
)

if TYPE_CHECKING:
    from synthorg.communication.config import MeetingsConfig, MeetingTypeConfig

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
        "_config",
        "_event_publisher",
        "_orchestrator",
        "_resolver",
        "_running",
        "_tasks",
    )

    def __init__(
        self,
        *,
        config: MeetingsConfig,
        orchestrator: MeetingOrchestrator,
        participant_resolver: ParticipantResolver,
        event_publisher: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._config = config
        self._orchestrator = orchestrator
        self._resolver = participant_resolver
        self._event_publisher = event_publisher
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False

    @property
    def running(self) -> bool:
        """Whether the scheduler is currently running."""
        return self._running

    async def start(self) -> None:
        """Start periodic tasks for all frequency-based meeting types.

        No-op if ``config.enabled`` is False.

        Raises:
            SchedulerAlreadyRunningError: If the scheduler is already running.
        """
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

        self._running = True

        scheduled = self.get_scheduled_types()
        self._tasks = [
            asyncio.create_task(
                self._run_periodic(mt),
                name=f"meeting-{mt.name}",
            )
            for mt in scheduled
        ]

        logger.info(
            MEETING_SCHEDULER_STARTED,
            periodic_count=len(scheduled),
            triggered_count=len(self.get_triggered_types()),
        )

    async def stop(self) -> None:
        """Cancel all periodic tasks and wait for completion."""
        if not self._running:
            return

        for task in self._tasks:
            task.cancel()
        if self._tasks:
            results = await asyncio.gather(
                *self._tasks,
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, asyncio.CancelledError):
                    continue
                if isinstance(result, Exception):
                    logger.warning(
                        MEETING_SCHEDULER_ERROR,
                        note="periodic task error during shutdown",
                        error=str(result),
                        error_type=type(result).__name__,
                    )
        self._tasks = []
        self._running = False

        logger.info(MEETING_SCHEDULER_STOPPED)

    async def trigger_event(
        self,
        event_name: str,
        *,
        context: dict[str, Any] | None = None,
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

        logger.info(
            MEETING_EVENT_TRIGGERED,
            event_name=event_name,
            matching_count=len(matching),
        )

        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(self._execute_meeting(mt, context)) for mt in matching
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
            while True:
                await asyncio.sleep(interval)
                logger.info(
                    MEETING_PERIODIC_TRIGGERED,
                    meeting_type=meeting_type.name,
                    interval_seconds=interval,
                )
                try:
                    await self._execute_meeting(meeting_type)
                except MemoryError, RecursionError:
                    raise
                except Exception:
                    logger.exception(
                        MEETING_SCHEDULER_ERROR,
                        meeting_type=meeting_type.name,
                        note="periodic execution failed",
                    )
        except asyncio.CancelledError:  # noqa: TRY203
            raise

    async def _execute_meeting(
        self,
        meeting_type: MeetingTypeConfig,
        context: dict[str, Any] | None = None,
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
        except Exception:
            logger.exception(
                MEETING_SCHEDULER_ERROR,
                meeting_type=meeting_type.name,
                note="agenda build failed",
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
        context: dict[str, Any] | None,
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
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                MEETING_SCHEDULER_ERROR,
                meeting_type=meeting_type.name,
                note="participant resolution failed",
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
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                MEETING_SCHEDULER_ERROR,
                meeting_type=meeting_type.name,
                note="orchestrator execution failed",
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
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                MEETING_SCHEDULER_ERROR,
                meeting_id=record.meeting_id,
                meeting_type=record.meeting_type_name,
                note="event publisher failed",
                exc_info=True,
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
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                MEETING_SCHEDULER_ERROR,
                meeting_type=meeting_type_name,
                note="started event publish failed",
                exc_info=True,
            )

    @staticmethod
    def _build_default_agenda(
        meeting_type: MeetingTypeConfig,
        context: dict[str, Any] | None,
    ) -> MeetingAgenda:
        """Create a default agenda from meeting type name and context.

        Args:
            meeting_type: The meeting type configuration.
            context: Optional context dict — keys become agenda items.

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


def _format_ctx_value(value: Any) -> str:
    """Format a context value as a human-readable string.

    Lists/tuples/sets are comma-joined; scalars use ``str()``.
    Strings and bytes are not treated as iterables.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(str(item) for item in value)
    return str(value)
