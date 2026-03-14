"""Tests for MeetingScheduler."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.communication.config import MeetingsConfig, MeetingTypeConfig
from synthorg.communication.meeting.enums import (
    MeetingProtocolType,
    MeetingStatus,
)
from synthorg.communication.meeting.errors import (
    NoParticipantsResolvedError,
    SchedulerAlreadyRunningError,
)
from synthorg.communication.meeting.frequency import MeetingFrequency
from synthorg.communication.meeting.models import (
    MeetingAgenda,
    MeetingMinutes,
    MeetingRecord,
)
from synthorg.communication.meeting.scheduler import MeetingScheduler


def _make_minutes() -> MeetingMinutes:
    """Create minimal valid MeetingMinutes."""
    now = datetime.now(UTC)
    return MeetingMinutes(
        meeting_id="mtg-test123",
        protocol_type=MeetingProtocolType.ROUND_ROBIN,
        leader_id="leader-id",
        participant_ids=("participant-1",),
        agenda=MeetingAgenda(title="Test"),
        started_at=now,
        ended_at=now,
    )


def _make_record(
    meeting_type: str = "standup",
    status: MeetingStatus = MeetingStatus.COMPLETED,
) -> MeetingRecord:
    """Create a fake MeetingRecord for testing."""
    return MeetingRecord(
        meeting_id="mtg-test123",
        meeting_type_name=meeting_type,
        protocol_type=MeetingProtocolType.ROUND_ROBIN,
        status=status,
        token_budget=2000,
        minutes=_make_minutes() if status == MeetingStatus.COMPLETED else None,
        error_message="test error" if status == MeetingStatus.FAILED else None,
    )


def _make_config(
    *,
    enabled: bool = True,
    types: tuple[MeetingTypeConfig, ...] = (),
) -> MeetingsConfig:
    return MeetingsConfig(enabled=enabled, types=types)


def _make_frequency_type(
    name: str = "standup",
    frequency: MeetingFrequency = MeetingFrequency.DAILY,
    participants: tuple[str, ...] = ("engineering",),
) -> MeetingTypeConfig:
    return MeetingTypeConfig(
        name=name,
        frequency=frequency,
        participants=participants,
    )


def _make_trigger_type(
    name: str = "review",
    trigger: str = "code_review_complete",
    participants: tuple[str, ...] = ("engineering",),
) -> MeetingTypeConfig:
    return MeetingTypeConfig(
        name=name,
        trigger=trigger,
        participants=participants,
    )


@pytest.mark.unit
class TestMeetingScheduler:
    """Tests for MeetingScheduler."""

    @pytest.fixture
    def orchestrator(self) -> MagicMock:
        orch = MagicMock()
        orch.run_meeting = AsyncMock(
            return_value=_make_record(),
        )
        orch.get_records = MagicMock(return_value=())
        return orch

    @pytest.fixture
    def resolver(self) -> MagicMock:
        res = MagicMock()
        res.resolve = AsyncMock(
            return_value=("leader-id", "participant-1", "participant-2"),
        )
        return res

    def _make_scheduler(
        self,
        config: MeetingsConfig,
        orchestrator: MagicMock,
        resolver: MagicMock,
        event_publisher: MagicMock | None = None,
    ) -> MeetingScheduler:
        return MeetingScheduler(
            config=config,
            orchestrator=orchestrator,
            participant_resolver=resolver,
            event_publisher=event_publisher,
        )

    async def test_start_creates_periodic_tasks(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        freq_type = _make_frequency_type()
        config = _make_config(types=(freq_type,))
        scheduler = self._make_scheduler(config, orchestrator, resolver)

        await scheduler.start()
        assert scheduler.running is True

        await scheduler.stop()
        assert scheduler.running is False

    async def test_start_raises_when_already_running(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        config = _make_config(types=(_make_frequency_type(),))
        scheduler = self._make_scheduler(config, orchestrator, resolver)

        await scheduler.start()
        try:
            with pytest.raises(SchedulerAlreadyRunningError):
                await scheduler.start()
        finally:
            await scheduler.stop()

    async def test_start_noop_when_disabled(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        config = _make_config(enabled=False, types=(_make_frequency_type(),))
        scheduler = self._make_scheduler(config, orchestrator, resolver)

        await scheduler.start()

        assert scheduler.running is False

    async def test_stop_cancels_all_tasks(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        config = _make_config(
            types=(
                _make_frequency_type("standup"),
                _make_frequency_type("retro", MeetingFrequency.WEEKLY),
            ),
        )
        scheduler = self._make_scheduler(config, orchestrator, resolver)

        await scheduler.start()
        assert scheduler.running is True

        await scheduler.stop()
        assert scheduler.running is False

    async def test_trigger_event_matches_types(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        trigger_type = _make_trigger_type()
        config = _make_config(types=(trigger_type,))
        scheduler = self._make_scheduler(config, orchestrator, resolver)

        records = await scheduler.trigger_event("code_review_complete")

        assert len(records) == 1
        orchestrator.run_meeting.assert_awaited_once()

    async def test_trigger_event_returns_empty_for_unknown(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        config = _make_config(types=(_make_trigger_type(),))
        scheduler = self._make_scheduler(config, orchestrator, resolver)

        records = await scheduler.trigger_event("unknown_event")

        assert records == ()
        orchestrator.run_meeting.assert_not_awaited()

    async def test_trigger_event_passes_context(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        trigger_type = _make_trigger_type()
        config = _make_config(types=(trigger_type,))
        scheduler = self._make_scheduler(config, orchestrator, resolver)

        ctx = {"author": "agent-123"}
        await scheduler.trigger_event("code_review_complete", context=ctx)

        resolver.resolve.assert_awaited_once_with(
            trigger_type.participants,
            ctx,
        )

    async def test_execute_resolves_participants_picks_leader(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        trigger_type = _make_trigger_type()
        config = _make_config(types=(trigger_type,))
        scheduler = self._make_scheduler(config, orchestrator, resolver)

        await scheduler.trigger_event("code_review_complete")

        call_kwargs = orchestrator.run_meeting.call_args.kwargs
        assert call_kwargs["leader_id"] == "leader-id"
        assert call_kwargs["participant_ids"] == (
            "participant-1",
            "participant-2",
        )

    async def test_execute_skips_with_too_few_participants(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        resolver.resolve.return_value = ("only-one",)
        trigger_type = _make_trigger_type()
        config = _make_config(types=(trigger_type,))
        scheduler = self._make_scheduler(config, orchestrator, resolver)

        records = await scheduler.trigger_event("code_review_complete")

        assert len(records) == 0
        orchestrator.run_meeting.assert_not_awaited()

    async def test_execute_handles_orchestrator_error(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        orchestrator.run_meeting.side_effect = RuntimeError("boom")
        trigger_type = _make_trigger_type()
        config = _make_config(types=(trigger_type,))
        scheduler = self._make_scheduler(config, orchestrator, resolver)

        records = await scheduler.trigger_event("code_review_complete")

        assert len(records) == 0

    async def test_execute_handles_no_participants_resolved_error(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        resolver.resolve.side_effect = NoParticipantsResolvedError(
            "no participants",
        )
        trigger_type = _make_trigger_type()
        config = _make_config(types=(trigger_type,))
        scheduler = self._make_scheduler(config, orchestrator, resolver)

        records = await scheduler.trigger_event("code_review_complete")

        assert len(records) == 0
        orchestrator.run_meeting.assert_not_awaited()

    def test_build_default_agenda(self) -> None:
        meeting_type = _make_trigger_type(name="code_review")
        agenda = MeetingScheduler._build_default_agenda(
            meeting_type,
            {"pr_url": "https://example.com/pr/1"},
        )

        assert agenda.title == "code_review"
        assert len(agenda.items) == 1
        assert agenda.items[0].title == "pr_url"

    def test_build_default_agenda_no_context(self) -> None:
        meeting_type = _make_trigger_type(name="standup")
        agenda = MeetingScheduler._build_default_agenda(
            meeting_type,
            None,
        )

        assert agenda.title == "standup"
        assert len(agenda.items) == 0
        assert agenda.context == ""

    def test_get_scheduled_types(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        freq = _make_frequency_type()
        trig = _make_trigger_type()
        config = _make_config(types=(freq, trig))
        scheduler = self._make_scheduler(config, orchestrator, resolver)

        assert scheduler.get_scheduled_types() == (freq,)

    def test_get_triggered_types(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        freq = _make_frequency_type()
        trig = _make_trigger_type()
        config = _make_config(types=(freq, trig))
        scheduler = self._make_scheduler(config, orchestrator, resolver)

        assert scheduler.get_triggered_types() == (trig,)

    async def test_stop_noop_when_not_running(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        config = _make_config(types=(_make_frequency_type(),))
        scheduler = self._make_scheduler(config, orchestrator, resolver)

        # stop() on a never-started scheduler should not raise
        await scheduler.stop()
        assert scheduler.running is False

    async def test_publish_event_error_does_not_prevent_record(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        publisher = MagicMock(side_effect=RuntimeError("publish failed"))
        trigger_type = _make_trigger_type()
        config = _make_config(types=(trigger_type,))
        scheduler = self._make_scheduler(
            config,
            orchestrator,
            resolver,
            event_publisher=publisher,
        )

        records = await scheduler.trigger_event("code_review_complete")

        assert len(records) == 1
        # Both started and completed publish calls are attempted and swallowed.
        assert publisher.call_count == 2

    async def test_publish_event_reraises_memory_error(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        publisher = MagicMock(side_effect=MemoryError)
        trigger_type = _make_trigger_type()
        config = _make_config(types=(trigger_type,))
        scheduler = self._make_scheduler(
            config,
            orchestrator,
            resolver,
            event_publisher=publisher,
        )

        with pytest.raises(ExceptionGroup):
            await scheduler.trigger_event("code_review_complete")

    async def test_event_publisher_called(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        publisher = MagicMock()
        trigger_type = _make_trigger_type()
        config = _make_config(types=(trigger_type,))
        scheduler = self._make_scheduler(
            config,
            orchestrator,
            resolver,
            event_publisher=publisher,
        )

        await scheduler.trigger_event("code_review_complete")

        assert publisher.call_count == 2
        # First call: meeting.started (before run_meeting)
        assert publisher.call_args_list[0][0][0] == "meeting.started"
        # Second call: meeting.completed (after run_meeting)
        assert publisher.call_args_list[1][0][0] == "meeting.completed"
