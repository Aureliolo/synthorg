"""Per-sprint ceremony meeting types registered on the scheduler.

A ceremony fires ``ceremony.<name>.<sprint_id>``, an event name no static
``meetings.types`` block can carry because the sprint id is only known at
runtime. The scheduler therefore accepts a per-sprint set alongside its
constructed config, and matches triggers against both.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.communication.config import MeetingsConfig
from synthorg.communication.meeting.config import MeetingTypeConfig
from synthorg.communication.meeting.enums import MeetingProtocolType, MeetingStatus
from synthorg.communication.meeting.errors import MeetingCeremonyRegistrationError
from synthorg.communication.meeting.frequency import MeetingFrequency
from synthorg.communication.meeting.models import (
    MeetingAgenda,
    MeetingMinutes,
    MeetingRecord,
)
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.communication.meeting.participant import ParticipantResolver
from synthorg.communication.meeting.scheduler import MeetingScheduler
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _record() -> MeetingRecord:
    now = datetime.now(UTC)
    return MeetingRecord(
        meeting_id="mtg-ceremony",
        meeting_type_name="daily_standup",
        protocol_type=MeetingProtocolType.ROUND_ROBIN,
        status=MeetingStatus.COMPLETED,
        token_budget=2000,
        minutes=MeetingMinutes(
            meeting_id="mtg-ceremony",
            protocol_type=MeetingProtocolType.ROUND_ROBIN,
            leader_id="leader-id",
            participant_ids=("participant-1",),
            agenda=MeetingAgenda(title="Standup"),
            started_at=now,
            ended_at=now,
        ),
    )


def _run_meeting(orchestrator: MeetingOrchestrator) -> AsyncMock:
    """Return the double standing in for ``run_meeting``.

    Returns:
        The ``AsyncMock`` the fixture installed, for await assertions.
    """
    call = orchestrator.run_meeting
    assert isinstance(call, AsyncMock)
    return call


def _ceremony_type(
    name: str = "daily_standup",
    sprint_id: str = "sprint-1",
) -> MeetingTypeConfig:
    return MeetingTypeConfig(
        name=name,
        trigger=f"ceremony.{name}.{sprint_id}",
        participants=("engineering",),
    )


class TestCeremonyTypeRegistration:
    """Registration decides which ceremony events the scheduler answers."""

    @pytest.fixture
    def orchestrator(self) -> MeetingOrchestrator:
        double: MeetingOrchestrator = mock_of[MeetingOrchestrator](
            run_meeting=AsyncMock(
                spec=MeetingOrchestrator.run_meeting, return_value=_record()
            ),
        )
        return double

    @pytest.fixture
    def resolver(self) -> ParticipantResolver:
        double: ParticipantResolver = mock_of[ParticipantResolver](
            resolve=AsyncMock(
                spec=ParticipantResolver.resolve,
                return_value=("leader-id", "participant-1"),
            ),
        )
        return double

    def _scheduler(
        self,
        orchestrator: MeetingOrchestrator,
        resolver: ParticipantResolver,
        types: tuple[MeetingTypeConfig, ...] = (),
    ) -> MeetingScheduler:
        return MeetingScheduler(
            config=MeetingsConfig(enabled=True, types=types),
            orchestrator=orchestrator,
            participant_resolver=resolver,
        )

    async def test_unregistered_ceremony_event_matches_nothing(
        self,
        orchestrator: MeetingOrchestrator,
        resolver: ParticipantResolver,
    ) -> None:
        """The state this change exists to end: a trigger nothing answers."""
        scheduler = self._scheduler(orchestrator, resolver)

        records = await scheduler.trigger_event("ceremony.daily_standup.sprint-1")

        assert records == ()
        _run_meeting(orchestrator).assert_not_awaited()

    async def test_registered_ceremony_event_runs_a_meeting(
        self,
        orchestrator: MeetingOrchestrator,
        resolver: ParticipantResolver,
    ) -> None:
        scheduler = self._scheduler(orchestrator, resolver)
        scheduler.register_ceremony_types((_ceremony_type(),))

        records = await scheduler.trigger_event("ceremony.daily_standup.sprint-1")

        assert len(records) == 1
        _run_meeting(orchestrator).assert_awaited_once()

    async def test_clear_unmatches_the_event(
        self,
        orchestrator: MeetingOrchestrator,
        resolver: ParticipantResolver,
    ) -> None:
        scheduler = self._scheduler(orchestrator, resolver)
        scheduler.register_ceremony_types((_ceremony_type(),))
        await scheduler.clear_ceremony_types()

        records = await scheduler.trigger_event("ceremony.daily_standup.sprint-1")

        assert records == ()
        _run_meeting(orchestrator).assert_not_awaited()

    async def test_clear_drops_the_ceremony_cooldown(
        self,
        orchestrator: MeetingOrchestrator,
        resolver: ParticipantResolver,
    ) -> None:
        """Sprints reuse ceremony names, and cooldowns are keyed by name."""
        scheduler = self._scheduler(orchestrator, resolver)
        cooling = MeetingTypeConfig(
            name="daily_standup",
            trigger="ceremony.daily_standup.sprint-1",
            participants=("engineering",),
            min_interval_seconds=3600,
        )
        scheduler.register_ceremony_types((cooling,))
        first = await scheduler.trigger_event("ceremony.daily_standup.sprint-1")
        assert len(first) == 1

        await scheduler.clear_ceremony_types()
        scheduler.register_ceremony_types(
            (cooling.model_copy(update={"trigger": "ceremony.daily_standup.sprint-2"}),)
        )
        next_sprint = await scheduler.trigger_event("ceremony.daily_standup.sprint-2")

        # Without the cooldown going with the cleared type, sprint-2's
        # first standup would be suppressed by sprint-1's.
        assert len(next_sprint) == 1

    async def test_registration_replaces_the_previous_sprint(
        self,
        orchestrator: MeetingOrchestrator,
        resolver: ParticipantResolver,
    ) -> None:
        """A sprint owns its ceremonies wholesale, so the set is replaced."""
        scheduler = self._scheduler(orchestrator, resolver)
        scheduler.register_ceremony_types((_ceremony_type(sprint_id="sprint-1"),))
        scheduler.register_ceremony_types((_ceremony_type(sprint_id="sprint-2"),))

        stale = await scheduler.trigger_event("ceremony.daily_standup.sprint-1")
        current = await scheduler.trigger_event("ceremony.daily_standup.sprint-2")

        assert stale == ()
        assert len(current) == 1

    async def test_static_config_types_still_match(
        self,
        orchestrator: MeetingOrchestrator,
        resolver: ParticipantResolver,
    ) -> None:
        """A hand-written meetings.types entry keeps working alongside."""
        static = MeetingTypeConfig(
            name="code_review",
            trigger="pr_opened",
            participants=("engineering",),
        )
        scheduler = self._scheduler(orchestrator, resolver, types=(static,))
        scheduler.register_ceremony_types((_ceremony_type(),))

        assert len(await scheduler.trigger_event("pr_opened")) == 1
        ceremony = await scheduler.trigger_event("ceremony.daily_standup.sprint-1")
        assert len(ceremony) == 1

    async def test_ceremony_types_are_never_scheduled_periodically(
        self,
        orchestrator: MeetingOrchestrator,
        resolver: ParticipantResolver,
    ) -> None:
        """Cadence stays with the ceremony scheduler; no second firing path."""
        scheduler = self._scheduler(orchestrator, resolver)
        scheduler.register_ceremony_types((_ceremony_type(),))

        assert scheduler.get_scheduled_types() == ()
        assert len(scheduler.get_triggered_types()) == 1

    def test_frequency_based_type_is_refused(
        self,
        orchestrator: MeetingOrchestrator,
        resolver: ParticipantResolver,
    ) -> None:
        scheduler = self._scheduler(orchestrator, resolver)
        periodic = MeetingTypeConfig(
            name="daily_standup",
            frequency=MeetingFrequency.DAILY,
        )

        with pytest.raises(MeetingCeremonyRegistrationError, match="trigger"):
            scheduler.register_ceremony_types((periodic,))

    def test_name_colliding_with_a_static_type_is_refused(
        self,
        orchestrator: MeetingOrchestrator,
        resolver: ParticipantResolver,
    ) -> None:
        """Cooldowns are keyed by name, so a shared name shares a cooldown."""
        static = MeetingTypeConfig(
            name="daily_standup",
            trigger="pr_opened",
        )
        scheduler = self._scheduler(orchestrator, resolver, types=(static,))

        with pytest.raises(MeetingCeremonyRegistrationError, match="daily_standup"):
            scheduler.register_ceremony_types((_ceremony_type(),))

    def test_a_refused_batch_registers_nothing(
        self,
        orchestrator: MeetingOrchestrator,
        resolver: ParticipantResolver,
    ) -> None:
        """Validation precedes installation, so a bad entry is not partial."""
        scheduler = self._scheduler(orchestrator, resolver)
        periodic = MeetingTypeConfig(
            name="retrospective",
            frequency=MeetingFrequency.DAILY,
        )

        with pytest.raises(MeetingCeremonyRegistrationError):
            scheduler.register_ceremony_types((_ceremony_type(), periodic))

        assert scheduler.get_triggered_types() == ()

    def test_a_repeated_trigger_within_one_batch_is_refused(
        self,
        orchestrator: MeetingOrchestrator,
        resolver: ParticipantResolver,
    ) -> None:
        """A trigger-keyed map would silently drop the earlier entry."""
        scheduler = self._scheduler(orchestrator, resolver)
        first = _ceremony_type()
        second = first.model_copy(update={"name": "standup_again"})

        with pytest.raises(MeetingCeremonyRegistrationError, match="repeats trigger"):
            scheduler.register_ceremony_types((first, second))

    def test_a_trigger_colliding_with_a_static_type_is_refused(
        self,
        orchestrator: MeetingOrchestrator,
        resolver: ParticipantResolver,
    ) -> None:
        """Both would fire on the one event rather than one shadowing."""
        static = MeetingTypeConfig(
            name="static_standup",
            trigger="ceremony.daily_standup.sprint-1",
        )
        scheduler = self._scheduler(orchestrator, resolver, types=(static,))

        with pytest.raises(MeetingCeremonyRegistrationError, match="already carries"):
            scheduler.register_ceremony_types((_ceremony_type(),))

    def test_validate_refuses_without_installing(
        self,
        orchestrator: MeetingOrchestrator,
        resolver: ParticipantResolver,
    ) -> None:
        """The pre-commit check a sprint start runs before it commits."""
        static = MeetingTypeConfig(name="daily_standup", trigger="pr_opened")
        scheduler = self._scheduler(orchestrator, resolver, types=(static,))

        with pytest.raises(MeetingCeremonyRegistrationError, match="daily_standup"):
            scheduler.validate_ceremony_types((_ceremony_type(),))

        assert scheduler.get_triggered_types() == (static,)

    def test_validate_accepts_a_registrable_batch_without_installing(
        self,
        orchestrator: MeetingOrchestrator,
        resolver: ParticipantResolver,
    ) -> None:
        scheduler = self._scheduler(orchestrator, resolver)

        scheduler.validate_ceremony_types((_ceremony_type(),))

        assert scheduler.get_triggered_types() == ()
