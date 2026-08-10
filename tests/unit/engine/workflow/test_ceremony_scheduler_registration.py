"""A sprint's ceremonies reach the meeting scheduler, and their config runs.

The ceremony scheduler dispatches ``ceremony.<name>.<sprint_id>``; nothing
matched it until the bridged meeting types were registered, so every ceremony
trigger returned no meetings. These drive the real ``MeetingScheduler`` rather
than a double, because the defect was in what the two agreed on.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.communication.config import MeetingsConfig
from synthorg.communication.meeting.config import (
    MeetingProtocolConfig,
    MeetingTypeConfig,
    StructuredPhasesConfig,
)
from synthorg.communication.meeting.enums import (
    ConflictDetectorType,
    MeetingProtocolType,
    MeetingStatus,
)
from synthorg.communication.meeting.errors import MeetingCeremonyRegistrationError
from synthorg.communication.meeting.frequency import MeetingFrequency
from synthorg.communication.meeting.models import (
    MeetingAgenda,
    MeetingMinutes,
    MeetingRecord,
)
from synthorg.communication.meeting.protocol import MeetingProtocol
from synthorg.communication.meeting.scheduler import MeetingScheduler
from synthorg.communication.meeting.structured_phases import StructuredPhasesProtocol
from synthorg.engine.workflow.ceremony_policy import (
    CeremonyPolicyConfig,
    CeremonyStrategyType,
)
from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler
from synthorg.engine.workflow.sprint_config import SprintCeremonyConfig, SprintConfig
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.strategies.task_driven import TaskDrivenStrategy

pytestmark = pytest.mark.unit


def _sprint(sprint_id: str = "sprint-1") -> Sprint:
    return Sprint.model_validate(
        {
            "id": sprint_id,
            "name": "Sprint 1",
            "sprint_number": 1,
            "status": SprintStatus.ACTIVE,
            "task_ids": ("task-0", "task-1"),
            "completed_task_ids": (),
            "story_points_committed": 6.0,
            "story_points_completed": 0.0,
            "start_date": "2026-04-01T00:00:00",
        }
    )


def _planning_ceremony() -> SprintCeremonyConfig:
    return SprintCeremonyConfig(
        name="sprint_planning",
        protocol=MeetingProtocolType.STRUCTURED_PHASES,
        frequency=MeetingFrequency.BI_WEEKLY,
        protocol_config=MeetingProtocolConfig(
            protocol=MeetingProtocolType.STRUCTURED_PHASES,
            structured_phases=StructuredPhasesConfig(
                conflict_detector=ConflictDetectorType.EMBEDDING,
                max_discussion_tokens=2000,
            ),
        ),
    )


def _sprint_config(
    ceremonies: tuple[SprintCeremonyConfig, ...],
) -> SprintConfig:
    return SprintConfig(
        ceremony_policy=CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.TASK_DRIVEN,
            auto_transition=True,
            transition_threshold=1.0,
        ),
        ceremonies=ceremonies,
    )


def _meeting_scheduler(
    orchestrator: MagicMock,
    types: tuple[MeetingTypeConfig, ...] = (),
) -> MeetingScheduler:
    resolver = MagicMock()
    resolver.resolve = AsyncMock(return_value=("leader-id", "participant-1"))
    return MeetingScheduler(
        config=MeetingsConfig(enabled=True, types=types),
        orchestrator=orchestrator,
        participant_resolver=resolver,
    )


def _orchestrator() -> MagicMock:
    now = datetime.now(UTC)
    record = MeetingRecord(
        meeting_id="mtg-ceremony",
        meeting_type_name="sprint_planning",
        protocol_type=MeetingProtocolType.STRUCTURED_PHASES,
        status=MeetingStatus.COMPLETED,
        token_budget=5000,
        minutes=MeetingMinutes(
            meeting_id="mtg-ceremony",
            protocol_type=MeetingProtocolType.STRUCTURED_PHASES,
            leader_id="leader-id",
            participant_ids=("participant-1",),
            agenda=MeetingAgenda(title="Sprint planning"),
            started_at=now,
            ended_at=now,
        ),
    )
    orch = MagicMock()
    orch.run_meeting = AsyncMock(return_value=record)
    orch.get_records = MagicMock(return_value=())
    return orch


class TestCeremonyTypeLifecycle:
    """Activation installs a sprint's ceremony types; deactivation drops them."""

    async def test_activation_registers_one_type_per_ceremony(self) -> None:
        meeting_scheduler = _meeting_scheduler(_orchestrator())
        scheduler = CeremonyScheduler(meeting_scheduler=meeting_scheduler)

        await scheduler.activate_sprint(
            _sprint(),
            _sprint_config((_planning_ceremony(),)),
            TaskDrivenStrategy(),
        )

        triggers = {mt.trigger for mt in meeting_scheduler.get_triggered_types()}
        assert triggers == {"ceremony.sprint_planning.sprint-1"}

    async def test_deactivation_clears_them(self) -> None:
        meeting_scheduler = _meeting_scheduler(_orchestrator())
        scheduler = CeremonyScheduler(meeting_scheduler=meeting_scheduler)
        await scheduler.activate_sprint(
            _sprint(),
            _sprint_config((_planning_ceremony(),)),
            TaskDrivenStrategy(),
        )

        await scheduler.deactivate_sprint()

        assert meeting_scheduler.get_triggered_types() == ()

    async def test_reactivation_replaces_the_previous_sprint(self) -> None:
        meeting_scheduler = _meeting_scheduler(_orchestrator())
        scheduler = CeremonyScheduler(meeting_scheduler=meeting_scheduler)
        await scheduler.activate_sprint(
            _sprint("sprint-1"),
            _sprint_config((_planning_ceremony(),)),
            TaskDrivenStrategy(),
        )

        await scheduler.activate_sprint(
            _sprint("sprint-2"),
            _sprint_config((_planning_ceremony(),)),
            TaskDrivenStrategy(),
        )

        triggers = {mt.trigger for mt in meeting_scheduler.get_triggered_types()}
        assert triggers == {"ceremony.sprint_planning.sprint-2"}

    async def test_failed_registration_rolls_the_activation_back(self) -> None:
        """A collision leaves no half-activated sprint behind."""
        colliding = MeetingTypeConfig(
            name="sprint_planning",
            trigger="manual_planning",
        )
        meeting_scheduler = _meeting_scheduler(_orchestrator(), types=(colliding,))
        scheduler = CeremonyScheduler(meeting_scheduler=meeting_scheduler)

        with pytest.raises(MeetingCeremonyRegistrationError):
            await scheduler.activate_sprint(
                _sprint(),
                _sprint_config((_planning_ceremony(),)),
                TaskDrivenStrategy(),
            )

        assert scheduler.running is False
        assert scheduler.active_sprint is None


class TestCeremonyProtocolConfigReachesTheMeeting:
    """The whole point: a ceremony's sub-config runs the meeting."""

    async def test_ceremony_config_reaches_the_protocol_instance(self) -> None:
        orchestrator = _orchestrator()
        meeting_scheduler = _meeting_scheduler(orchestrator)
        scheduler = CeremonyScheduler(meeting_scheduler=meeting_scheduler)
        await scheduler.activate_sprint(
            _sprint(),
            _sprint_config((_planning_ceremony(),)),
            TaskDrivenStrategy(),
        )

        await meeting_scheduler.trigger_event("ceremony.sprint_planning.sprint-1")

        orchestrator.run_meeting.assert_awaited_once()
        config = orchestrator.run_meeting.await_args.kwargs["protocol_config"]
        assert isinstance(config, MeetingProtocolConfig)
        assert config.protocol is MeetingProtocolType.STRUCTURED_PHASES
        assert (
            config.structured_phases.conflict_detector is ConflictDetectorType.EMBEDDING
        )
        assert config.structured_phases.max_discussion_tokens == 2000
        # The protocol the registry factory would build from it is the
        # one that acts on those values, so build it the same way the
        # orchestrator does and read them back off the instance.
        protocol = StructuredPhasesProtocol(config.structured_phases)
        assert isinstance(protocol, MeetingProtocol)
        assert protocol._config.max_discussion_tokens == 2000
