"""Tests for CeremonyScheduler service."""

from typing import override
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.communication.meeting.enums import MeetingProtocolType
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.ceremony_policy import (
    CeremonyPolicyConfig,
    CeremonyStrategyType,
)
from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler
from synthorg.engine.workflow.sprint_config import (
    SprintCeremonyConfig,
    SprintConfig,
)
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.strategies.calendar import CalendarStrategy
from synthorg.engine.workflow.strategies.task_driven import (
    TaskDrivenStrategy,
)
from synthorg.persistence.ceremony_scheduler_state_protocol import (
    CeremonySchedulerStateRecord,
)


def _make_sprint(
    task_count: int = 10,
    completed_count: int = 0,
    status: SprintStatus = SprintStatus.ACTIVE,
    points_per_task: float = 3.0,
) -> Sprint:
    task_ids = tuple(f"task-{i}" for i in range(task_count))
    completed_ids = tuple(f"task-{i}" for i in range(completed_count))
    kwargs: dict[str, object] = {
        "id": "sprint-1",
        "name": "Sprint 1",
        "sprint_number": 1,
        "status": status,
        "task_ids": task_ids,
        "completed_task_ids": completed_ids,
        "story_points_committed": float(task_count * points_per_task),
        "story_points_completed": float(completed_count * points_per_task),
    }
    if status is not SprintStatus.PLANNING:
        kwargs["start_date"] = "2026-04-01T00:00:00"
    if status is SprintStatus.COMPLETED:
        kwargs["end_date"] = "2026-04-14T00:00:00"
    return Sprint.model_validate(kwargs)


def _make_config(
    ceremonies: tuple[SprintCeremonyConfig, ...] = (),
    transition_threshold: float = 1.0,
) -> SprintConfig:
    return SprintConfig(
        ceremony_policy=CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.TASK_DRIVEN,
            auto_transition=True,
            transition_threshold=transition_threshold,
        ),
        ceremonies=ceremonies,
    )


def _ceremony_with_trigger(
    name: str,
    trigger: str,
    every_n: int = 5,
    sprint_percentage: float | None = None,
) -> SprintCeremonyConfig:
    config: dict[str, object] = {"trigger": trigger}
    if trigger == "every_n_completions":
        config["every_n_completions"] = every_n
    if sprint_percentage is not None:
        config["sprint_percentage"] = sprint_percentage
    return SprintCeremonyConfig(
        name=name,
        protocol=MeetingProtocolType.ROUND_ROBIN,
        policy_override=CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.TASK_DRIVEN,
            strategy_config=config,
        ),
    )


def _make_mock_meeting_scheduler() -> MagicMock:
    mock = MagicMock()
    mock.trigger_event = AsyncMock(return_value=())
    return mock


class TestCeremonySchedulerActivation:
    """activate_sprint / deactivate_sprint tests."""

    @pytest.mark.unit
    async def test_activate_sets_running(self) -> None:
        scheduler = CeremonyScheduler(
            meeting_scheduler=_make_mock_meeting_scheduler(),
        )
        sprint = _make_sprint()
        config = _make_config()
        await scheduler.activate_sprint(sprint, config, TaskDrivenStrategy())
        assert scheduler.running is True
        assert scheduler.active_sprint is sprint

    @pytest.mark.unit
    async def test_deactivate_clears_state(self) -> None:
        scheduler = CeremonyScheduler(
            meeting_scheduler=_make_mock_meeting_scheduler(),
        )
        await scheduler.activate_sprint(
            _make_sprint(),
            _make_config(),
            TaskDrivenStrategy(),
        )
        await scheduler.deactivate_sprint()
        assert scheduler.running is False
        assert scheduler.active_sprint is None

    @pytest.mark.unit
    async def test_activate_fires_sprint_start_ceremonies(self) -> None:
        mock_ms = _make_mock_meeting_scheduler()
        scheduler = CeremonyScheduler(meeting_scheduler=mock_ms)
        ceremony = _ceremony_with_trigger("planning", "sprint_start")
        config = _make_config(ceremonies=(ceremony,))
        await scheduler.activate_sprint(
            _make_sprint(),
            config,
            TaskDrivenStrategy(),
        )
        mock_ms.trigger_event.assert_called_once()
        args, _ = mock_ms.trigger_event.call_args
        assert args[0] == "ceremony.planning.sprint-1"

    @pytest.mark.unit
    async def test_double_activate_deactivates_first(self) -> None:
        scheduler = CeremonyScheduler(
            meeting_scheduler=_make_mock_meeting_scheduler(),
        )
        sprint1 = _make_sprint()
        sprint2 = _make_sprint()
        config = _make_config()
        strategy = TaskDrivenStrategy()
        await scheduler.activate_sprint(sprint1, config, strategy)
        await scheduler.activate_sprint(sprint2, config, strategy)
        assert scheduler.active_sprint is sprint2


class TestCeremonySchedulerTaskCompletion:
    """on_task_completed() tests."""

    @pytest.mark.unit
    async def test_fires_every_n_ceremony(self) -> None:
        mock_ms = _make_mock_meeting_scheduler()
        scheduler = CeremonyScheduler(meeting_scheduler=mock_ms)
        ceremony = _ceremony_with_trigger("standup", "every_n_completions", every_n=3)
        config = _make_config(ceremonies=(ceremony,))
        strategy = TaskDrivenStrategy()

        sprint = _make_sprint(task_count=10, completed_count=0)
        await scheduler.activate_sprint(sprint, config, strategy)

        # Simulate 3 task completions.
        for i in range(3):
            completed = i + 1
            sprint = _make_sprint(task_count=10, completed_count=completed)
            sprint = await scheduler.on_task_completed(
                sprint,
                f"task-{i}",
                3.0,
            )

        # The every_n_completions=3 ceremony should have fired exactly once.
        assert mock_ms.trigger_event.call_count == 1

    @pytest.mark.unit
    async def test_auto_transitions_sprint(self) -> None:
        mock_ms = _make_mock_meeting_scheduler()
        scheduler = CeremonyScheduler(meeting_scheduler=mock_ms)
        config = _make_config(transition_threshold=1.0)
        strategy = TaskDrivenStrategy()

        sprint = _make_sprint(task_count=2, completed_count=1)
        await scheduler.activate_sprint(sprint, config, strategy)

        # Complete the last task.
        final_sprint = _make_sprint(task_count=2, completed_count=2)
        result = await scheduler.on_task_completed(
            final_sprint,
            "task-1",
            3.0,
        )
        assert result.status is SprintStatus.IN_REVIEW

    @pytest.mark.unit
    async def test_no_transition_below_threshold(self) -> None:
        mock_ms = _make_mock_meeting_scheduler()
        scheduler = CeremonyScheduler(meeting_scheduler=mock_ms)
        config = _make_config(transition_threshold=1.0)
        strategy = TaskDrivenStrategy()

        sprint = _make_sprint(task_count=10, completed_count=5)
        await scheduler.activate_sprint(sprint, config, strategy)

        sprint = _make_sprint(task_count=10, completed_count=6)
        result = await scheduler.on_task_completed(
            sprint,
            "task-5",
            3.0,
        )
        assert result.status is SprintStatus.ACTIVE

    @pytest.mark.unit
    async def test_not_running_returns_sprint_unchanged(self) -> None:
        scheduler = CeremonyScheduler(
            meeting_scheduler=_make_mock_meeting_scheduler(),
        )
        sprint = _make_sprint(task_count=10, completed_count=5)
        result = await scheduler.on_task_completed(sprint, "task-4", 3.0)
        assert result is sprint

    @pytest.mark.unit
    async def test_sprint_midpoint_fires_once(self) -> None:
        mock_ms = _make_mock_meeting_scheduler()
        scheduler = CeremonyScheduler(meeting_scheduler=mock_ms)
        ceremony = _ceremony_with_trigger("midpoint_review", "sprint_midpoint")
        config = _make_config(ceremonies=(ceremony,))
        strategy = TaskDrivenStrategy()

        sprint = _make_sprint(task_count=4, completed_count=0)
        await scheduler.activate_sprint(sprint, config, strategy)

        # Complete task 1 (25%).
        sprint = _make_sprint(task_count=4, completed_count=1)
        await scheduler.on_task_completed(sprint, "task-0", 3.0)
        initial_count = mock_ms.trigger_event.call_count

        # Complete task 2 (50%) -- should fire midpoint.
        sprint = _make_sprint(task_count=4, completed_count=2)
        await scheduler.on_task_completed(sprint, "task-1", 3.0)
        after_midpoint_count = mock_ms.trigger_event.call_count
        assert after_midpoint_count == initial_count + 1

        # Complete task 3 (75%) -- should NOT fire midpoint again.
        sprint = _make_sprint(task_count=4, completed_count=3)
        await scheduler.on_task_completed(sprint, "task-2", 3.0)
        assert mock_ms.trigger_event.call_count == after_midpoint_count

    @pytest.mark.unit
    async def test_sprint_end_fires_once(self) -> None:
        mock_ms = _make_mock_meeting_scheduler()
        scheduler = CeremonyScheduler(meeting_scheduler=mock_ms)
        ceremony = _ceremony_with_trigger("retro", "sprint_end")
        config = _make_config(ceremonies=(ceremony,))
        strategy = TaskDrivenStrategy()

        sprint = _make_sprint(task_count=2, completed_count=0)
        await scheduler.activate_sprint(sprint, config, strategy)

        # Complete task 1 (50%).
        sprint = _make_sprint(task_count=2, completed_count=1)
        await scheduler.on_task_completed(sprint, "task-0", 3.0)
        count_before = mock_ms.trigger_event.call_count

        # Complete task 2 (100%) -- should fire sprint_end.
        sprint = _make_sprint(task_count=2, completed_count=2)
        await scheduler.on_task_completed(sprint, "task-1", 3.0)
        assert mock_ms.trigger_event.call_count == count_before + 1

    @pytest.mark.unit
    async def test_trigger_event_error_is_logged_and_swallowed(self) -> None:
        mock_ms = _make_mock_meeting_scheduler()
        mock_ms.trigger_event = AsyncMock(side_effect=RuntimeError("boom"))
        scheduler = CeremonyScheduler(meeting_scheduler=mock_ms)
        ceremony = _ceremony_with_trigger(
            "standup",
            "every_n_completions",
            every_n=1,
        )
        config = _make_config(ceremonies=(ceremony,))
        strategy = TaskDrivenStrategy()

        sprint = _make_sprint(task_count=10, completed_count=1)
        await scheduler.activate_sprint(sprint, config, strategy)

        # Should not raise despite trigger_event failing.
        sprint = _make_sprint(task_count=10, completed_count=2)
        result = await scheduler.on_task_completed(sprint, "task-1", 3.0)
        assert result.status is SprintStatus.ACTIVE

    @pytest.mark.unit
    async def test_memory_error_propagates(self) -> None:
        mock_ms = _make_mock_meeting_scheduler()
        mock_ms.trigger_event = AsyncMock(side_effect=MemoryError)
        scheduler = CeremonyScheduler(meeting_scheduler=mock_ms)
        ceremony = _ceremony_with_trigger(
            "standup",
            "every_n_completions",
            every_n=1,
        )
        config = _make_config(ceremonies=(ceremony,))
        strategy = TaskDrivenStrategy()

        sprint = _make_sprint(task_count=10, completed_count=1)
        await scheduler.activate_sprint(sprint, config, strategy)

        sprint = _make_sprint(task_count=10, completed_count=2)
        with pytest.raises(MemoryError):
            await scheduler.on_task_completed(sprint, "task-1", 3.0)

    @pytest.mark.unit
    async def test_deactivate_noop_when_not_running(self) -> None:
        scheduler = CeremonyScheduler(
            meeting_scheduler=_make_mock_meeting_scheduler(),
        )
        # Should not raise.
        await scheduler.deactivate_sprint()
        assert scheduler.running is False

    @pytest.mark.unit
    async def test_one_shot_not_marked_on_trigger_failure(self) -> None:
        """One-shot ceremonies must not be marked fired on failure.

        We verify by making trigger_event fail, then succeed on a
        second attempt -- the ceremony should fire again (proving it
        was not marked as fired).
        """
        mock_ms = _make_mock_meeting_scheduler()
        mock_ms.trigger_event = AsyncMock(
            side_effect=RuntimeError("boom"),
        )
        scheduler = CeremonyScheduler(meeting_scheduler=mock_ms)
        ceremony = _ceremony_with_trigger("retro", "sprint_end")
        config = _make_config(ceremonies=(ceremony,))
        strategy = TaskDrivenStrategy()

        sprint = _make_sprint(task_count=2, completed_count=0)
        await scheduler.activate_sprint(sprint, config, strategy)

        # Complete both tasks -- sprint_end should trigger but fail.
        sprint = _make_sprint(task_count=2, completed_count=1)
        await scheduler.on_task_completed(sprint, "task-0", 3.0)
        sprint = _make_sprint(task_count=2, completed_count=2)
        await scheduler.on_task_completed(sprint, "task-1", 3.0)
        # Now make trigger_event succeed and send another completion.
        mock_ms.trigger_event = AsyncMock(return_value=())
        sprint = _make_sprint(task_count=2, completed_count=2)
        await scheduler.on_task_completed(sprint, "task-1", 3.0)

        # The one-shot should fire again since it was not marked.
        assert mock_ms.trigger_event.call_count > 0

    @pytest.mark.unit
    async def test_activation_failure_cleans_up(self) -> None:
        """If strategy hook fails, scheduler deactivates."""
        mock_ms = _make_mock_meeting_scheduler()

        class FailingStrategy(TaskDrivenStrategy):
            @override
            async def on_sprint_activated(
                self,
                sprint: Sprint,
                config: SprintConfig,
            ) -> None:
                msg = "activation hook failed"
                raise RuntimeError(msg)

        scheduler = CeremonyScheduler(meeting_scheduler=mock_ms)
        sprint = _make_sprint()
        config = _make_config()

        with pytest.raises(RuntimeError, match="activation hook"):
            await scheduler.activate_sprint(
                sprint,
                config,
                FailingStrategy(),
            )

        assert scheduler.running is False
        assert scheduler.active_sprint is None


class TestCeremonySchedulerStrategyMigration:
    """Strategy migration detection via activate_sprint()."""

    @pytest.mark.unit
    async def test_first_activation_returns_none(self) -> None:
        """First activation has no previous strategy -- no migration."""
        scheduler = CeremonyScheduler(
            meeting_scheduler=_make_mock_meeting_scheduler(),
        )
        result = await scheduler.activate_sprint(
            _make_sprint(),
            _make_config(),
            TaskDrivenStrategy(),
        )
        assert result is None

    @pytest.mark.unit
    async def test_same_strategy_returns_none(self) -> None:
        """Re-activating with the same strategy type -- no migration."""
        scheduler = CeremonyScheduler(
            meeting_scheduler=_make_mock_meeting_scheduler(),
        )
        config = _make_config()
        strategy = TaskDrivenStrategy()
        await scheduler.activate_sprint(_make_sprint(), config, strategy)
        result = await scheduler.activate_sprint(
            _make_sprint(),
            config,
            TaskDrivenStrategy(),
        )
        assert result is None

    @pytest.mark.unit
    async def test_strategy_change_returns_migration_info(self) -> None:
        """Changing strategy type returns StrategyMigrationInfo."""
        scheduler = CeremonyScheduler(
            meeting_scheduler=_make_mock_meeting_scheduler(),
        )
        config = _make_config()
        await scheduler.activate_sprint(
            _make_sprint(),
            config,
            TaskDrivenStrategy(),
        )
        result = await scheduler.activate_sprint(
            _make_sprint(),
            config,
            CalendarStrategy(),
        )
        assert result is not None
        assert result.previous_strategy is CeremonyStrategyType.TASK_DRIVEN
        assert result.new_strategy is CeremonyStrategyType.CALENDAR
        assert result.sprint_id == "sprint-1"

    @pytest.mark.unit
    async def test_strategy_change_with_velocity_history(self) -> None:
        """Migration info includes velocity_history_size."""
        from synthorg.engine.workflow.sprint_velocity import VelocityRecord

        scheduler = CeremonyScheduler(
            meeting_scheduler=_make_mock_meeting_scheduler(),
        )
        config = _make_config()
        records = (
            VelocityRecord(
                sprint_id="s-0",
                sprint_number=1,
                story_points_committed=10.0,
                story_points_completed=8.0,
                duration_days=7,
            ),
        )
        await scheduler.activate_sprint(
            _make_sprint(),
            config,
            TaskDrivenStrategy(),
            velocity_history=records,
        )
        result = await scheduler.activate_sprint(
            _make_sprint(),
            config,
            CalendarStrategy(),
            velocity_history=records,
        )
        assert result is not None
        assert result.velocity_history_size == 1

    @pytest.mark.unit
    async def test_activation_failure_does_not_return_migration(self) -> None:
        """If activation fails, the exception propagates (no migration info)."""

        class FailingCalendarStrategy(CalendarStrategy):
            @override
            async def on_sprint_activated(
                self,
                sprint: Sprint,
                config: SprintConfig,
            ) -> None:
                msg = "calendar activation failed"
                raise RuntimeError(msg)

        scheduler = CeremonyScheduler(
            meeting_scheduler=_make_mock_meeting_scheduler(),
        )
        config = _make_config()
        await scheduler.activate_sprint(
            _make_sprint(),
            config,
            TaskDrivenStrategy(),
        )
        with pytest.raises(RuntimeError, match="calendar activation failed"):
            await scheduler.activate_sprint(
                _make_sprint(),
                config,
                FailingCalendarStrategy(),
            )

    @pytest.mark.unit
    async def test_strategy_after_failed_activation_is_cleared(self) -> None:
        """After a failed activation, the next activation is first-time."""
        scheduler = CeremonyScheduler(
            meeting_scheduler=_make_mock_meeting_scheduler(),
        )
        config = _make_config()
        await scheduler.activate_sprint(
            _make_sprint(),
            config,
            TaskDrivenStrategy(),
        )

        class FailingCalendarStrategy(CalendarStrategy):
            @override
            async def on_sprint_activated(
                self,
                sprint: Sprint,
                config: SprintConfig,
            ) -> None:
                msg = "fail"
                raise RuntimeError(msg)

        with pytest.raises(RuntimeError):
            await scheduler.activate_sprint(
                _make_sprint(),
                config,
                FailingCalendarStrategy(),
            )

        # After failure, next activation is treated as first-time.
        result = await scheduler.activate_sprint(
            _make_sprint(),
            config,
            CalendarStrategy(),
        )
        assert result is None


class _FakeStateRepo:
    """Minimal ``CeremonySchedulerStateRepository`` returning one record."""

    def __init__(self, record: CeremonySchedulerStateRecord | None) -> None:
        self._record = record

    async def save(self, entity: CeremonySchedulerStateRecord) -> None:
        self._record = entity

    async def get(self, entity_id: NotBlankStr) -> CeremonySchedulerStateRecord | None:
        return self._record

    async def delete(self, entity_id: NotBlankStr) -> bool:
        had = self._record is not None
        self._record = None
        return had

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[CeremonySchedulerStateRecord, ...]:
        if self._record is None:
            return ()
        return (self._record,)[offset : offset + limit]


def _state_record(
    *,
    counters_json: str,
    history_json: str = "[]",
) -> CeremonySchedulerStateRecord:
    from datetime import UTC, datetime

    return CeremonySchedulerStateRecord(
        sprint_id=NotBlankStr("sprint-1"),
        completion_counters_json=counters_json,
        fired_once_triggers_json="[]",
        total_completions=0,
        velocity_history_json=history_json,
        updated_at=datetime.now(UTC),
    )


@pytest.mark.unit
class TestCeremonySchedulerHydrationTolerance:
    """``_hydrate_state_from_repo`` must tolerate partial/corrupt rows."""

    async def test_merges_persisted_counters_onto_seeded_map(self) -> None:
        """A persisted snapshot missing a config ceremony keeps its seed.

        Stale snapshot keys for removed ceremonies are ignored; counts
        for current ceremonies are overwritten from the snapshot.
        """
        scheduler = CeremonyScheduler(
            meeting_scheduler=_make_mock_meeting_scheduler(),
            state_repo=_FakeStateRepo(
                _state_record(counters_json='{"alpha": 7, "gamma": 99}')
            ),
        )
        # Simulate the fresh seed activate_sprint sets for current config.
        scheduler._completion_counters = {"alpha": 0, "beta": 0}

        await scheduler._hydrate_state_from_repo("sprint-1")

        assert scheduler._completion_counters == {"alpha": 7, "beta": 0}

    async def test_corrupt_velocity_history_keeps_fresh_state(self) -> None:
        """A row that fails VelocityRecord validation retains zeroed state."""
        scheduler = CeremonyScheduler(
            meeting_scheduler=_make_mock_meeting_scheduler(),
            state_repo=_FakeStateRepo(
                _state_record(
                    counters_json='{"alpha": 5}',
                    history_json='[{"not": "a-velocity-record"}]',
                )
            ),
        )
        scheduler._completion_counters = {"alpha": 0}

        # Must not raise; fresh seed retained because the row is corrupt.
        await scheduler._hydrate_state_from_repo("sprint-1")

        assert scheduler._completion_counters == {"alpha": 0}
