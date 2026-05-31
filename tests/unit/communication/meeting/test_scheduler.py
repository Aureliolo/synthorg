"""Tests for MeetingScheduler."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.communication.config import MeetingsConfig
from synthorg.communication.meeting.config import MeetingTypeConfig
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
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.meeting_cooldown_protocol import MeetingCooldownRecord


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
    min_interval_seconds: int | None = None,
) -> MeetingTypeConfig:
    return MeetingTypeConfig(
        name=name,
        trigger=trigger,
        participants=participants,
        min_interval_seconds=min_interval_seconds,
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


@pytest.mark.unit
class TestMeetingSchedulerCooldown:
    """Tests for event-triggered meeting cooldown guard."""

    @pytest.fixture
    def orchestrator(self) -> MagicMock:
        orch = MagicMock()
        orch.run_meeting = AsyncMock(return_value=_make_record())
        return orch

    @pytest.fixture
    def resolver(self) -> MagicMock:
        res = MagicMock()
        res.resolve = AsyncMock(
            return_value=("leader-id", "participant-1", "participant-2"),
        )
        return res

    async def test_cooldown_skips_second_trigger(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        clock_time = 1000.0

        def clock() -> float:
            return clock_time

        trigger_type = _make_trigger_type(min_interval_seconds=60)
        config = _make_config(types=(trigger_type,))
        scheduler = MeetingScheduler(
            config=config,
            orchestrator=orchestrator,
            participant_resolver=resolver,
            clock=clock,
        )

        records = await scheduler.trigger_event("code_review_complete")
        assert len(records) == 1

        # Second trigger at same time -- should be skipped
        records = await scheduler.trigger_event("code_review_complete")
        assert len(records) == 0

    async def test_cooldown_allows_after_expiry(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        clock_time = 1000.0

        def clock() -> float:
            return clock_time

        trigger_type = _make_trigger_type(min_interval_seconds=60)
        config = _make_config(types=(trigger_type,))
        scheduler = MeetingScheduler(
            config=config,
            orchestrator=orchestrator,
            participant_resolver=resolver,
            clock=clock,
        )

        await scheduler.trigger_event("code_review_complete")

        clock_time = 1061.0  # 61s later
        records = await scheduler.trigger_event("code_review_complete")
        assert len(records) == 1

    async def test_no_cooldown_fires_immediately(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        """Default (no cooldown) triggers every time."""
        trigger_type = _make_trigger_type()  # min_interval_seconds=None
        config = _make_config(types=(trigger_type,))
        scheduler = MeetingScheduler(
            config=config,
            orchestrator=orchestrator,
            participant_resolver=resolver,
        )

        await scheduler.trigger_event("code_review_complete")
        records = await scheduler.trigger_event("code_review_complete")
        assert len(records) == 1
        assert orchestrator.run_meeting.await_count == 2

    async def test_independent_cooldowns_per_type(
        self,
        orchestrator: MagicMock,
        resolver: MagicMock,
    ) -> None:
        clock_time = 1000.0

        def clock() -> float:
            return clock_time

        type_a = _make_trigger_type(
            name="review_short",
            trigger="task_done",
            min_interval_seconds=30,
        )
        type_b = _make_trigger_type(
            name="review_long",
            trigger="task_done",
            min_interval_seconds=120,
        )
        config = _make_config(types=(type_a, type_b))
        scheduler = MeetingScheduler(
            config=config,
            orchestrator=orchestrator,
            participant_resolver=resolver,
            clock=clock,
        )

        # First trigger fires both
        records = await scheduler.trigger_event("task_done")
        assert len(records) == 2

        # Advance 31s -- only type_a should fire
        clock_time = 1031.0
        records = await scheduler.trigger_event("task_done")
        assert len(records) == 1


class _FakeCooldownRepo:
    """In-memory ``MeetingCooldownRepository`` double for scheduler tests."""

    def __init__(
        self,
        *,
        initial: tuple[MeetingCooldownRecord, ...] = (),
        fail_save: bool = False,
        fail_load: bool = False,
    ) -> None:
        self._rows: dict[str, MeetingCooldownRecord] = {
            r.meeting_type_name: r for r in initial
        }
        self._fail_save = fail_save
        self._fail_load = fail_load
        self.saved: list[MeetingCooldownRecord] = []

    async def load_all(self) -> tuple[MeetingCooldownRecord, ...]:
        if self._fail_load:
            msg = "cooldown read failed"
            raise QueryError(msg)
        return tuple(self._rows.values())

    async def save(self, entity: MeetingCooldownRecord) -> None:
        if self._fail_save:
            msg = "cooldown write failed"
            raise QueryError(msg)
        self.saved.append(entity)
        self._rows[entity.meeting_type_name] = entity

    async def get(self, entity_id: NotBlankStr) -> MeetingCooldownRecord | None:
        return self._rows.get(entity_id)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._rows.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[MeetingCooldownRecord, ...]:
        return tuple(self._rows.values())[offset : offset + limit]


@pytest.mark.unit
class TestMeetingSchedulerCooldownPersistence:
    """Cooldown durability: persist re-raise + hydrate replace semantics."""

    @pytest.fixture
    def orchestrator(self) -> MagicMock:
        orch = MagicMock()
        orch.run_meeting = AsyncMock(return_value=_make_record())
        return orch

    @pytest.fixture
    def resolver(self) -> MagicMock:
        res = MagicMock()
        res.resolve = AsyncMock(
            return_value=("leader-id", "participant-1", "participant-2"),
        )
        return res

    async def test_persist_failure_propagates_and_skips_in_memory_set(
        self, orchestrator: MagicMock, resolver: MagicMock
    ) -> None:
        """A cooldown persist failure re-raises before the in-memory set.

        Persisting must happen before ``_last_triggered`` is updated so a
        write failure cannot leave a phantom cooldown that vanishes on
        restart and lets the meeting immediately re-fire.
        """
        trigger_type = _make_trigger_type(min_interval_seconds=60)
        config = _make_config(types=(trigger_type,))
        scheduler = MeetingScheduler(
            config=config,
            orchestrator=orchestrator,
            participant_resolver=resolver,
            cooldown_repo=_FakeCooldownRepo(fail_save=True),
        )

        with pytest.raises(QueryError):
            await scheduler.trigger_event("code_review_complete")

        assert scheduler._last_triggered == {}

    async def test_hydrate_replaces_stale_in_memory_entries(
        self, orchestrator: MagicMock, resolver: MagicMock
    ) -> None:
        """Hydration drops in-memory types absent from the persisted set."""
        persisted = MeetingCooldownRecord(
            meeting_type_name=NotBlankStr("persisted_type"),
            last_triggered_at=datetime.now(UTC),
        )
        config = _make_config(types=(_make_trigger_type(),))
        scheduler = MeetingScheduler(
            config=config,
            orchestrator=orchestrator,
            participant_resolver=resolver,
            cooldown_repo=_FakeCooldownRepo(initial=(persisted,)),
        )
        scheduler._last_triggered["stale_type"] = 999.0

        await scheduler._hydrate_cooldowns_from_repo()

        assert "stale_type" not in scheduler._last_triggered
        assert "persisted_type" in scheduler._last_triggered

    async def test_hydrate_failure_aborts_start(
        self, orchestrator: MagicMock, resolver: MagicMock
    ) -> None:
        """A cooldown load failure aborts startup instead of running empty.

        Continuing with an empty ``_last_triggered`` would silently drop
        the persisted cooldown floor and let an event-triggered meeting
        re-fire immediately after a restart, so the failure must surface.
        """
        config = _make_config(types=(_make_trigger_type(),))
        scheduler = MeetingScheduler(
            config=config,
            orchestrator=orchestrator,
            participant_resolver=resolver,
            cooldown_repo=_FakeCooldownRepo(fail_load=True),
        )

        with pytest.raises(QueryError):
            await scheduler.start()

        assert scheduler.running is False
        assert scheduler._last_triggered == {}
