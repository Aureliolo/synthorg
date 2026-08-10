"""A sprint's ceremonies reach the meeting scheduler, and their config runs.

The ceremony scheduler dispatches ``ceremony.<name>.<sprint_id>``, an event
name only the bridged meeting types installed on ``activate_sprint`` match.
These drive the real ``MeetingScheduler`` rather than a double, because the
invariant under test is that the two agree on that trigger name.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

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
from synthorg.communication.meeting.errors import (
    MeetingCeremonyRegistrationError,
    MeetingProtocolNotFoundError,
)
from synthorg.communication.meeting.frequency import MeetingFrequency
from synthorg.communication.meeting.models import (
    MeetingAgenda,
    MeetingMinutes,
    MeetingRecord,
)
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.communication.meeting.participant import ParticipantResolver
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
from synthorg.observability.events.workflow import SPRINT_CEREMONY_TRIGGER_FAILED
from tests._shared import mock_of

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
    orchestrator: MeetingOrchestrator,
    types: tuple[MeetingTypeConfig, ...] = (),
) -> MeetingScheduler:
    resolver: ParticipantResolver = mock_of[ParticipantResolver](
        resolve=AsyncMock(
            spec=ParticipantResolver.resolve,
            return_value=("leader-id", "participant-1"),
        ),
    )
    return MeetingScheduler(
        config=MeetingsConfig(enabled=True, types=types),
        orchestrator=orchestrator,
        participant_resolver=resolver,
    )


def _run_meeting(orchestrator: MeetingOrchestrator) -> AsyncMock:
    """Return the double standing in for ``run_meeting``.

    Returns:
        The ``AsyncMock`` the builder installed, for await assertions.
    """
    call = orchestrator.run_meeting
    assert isinstance(call, AsyncMock)
    return call


def _orchestrator() -> MeetingOrchestrator:
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
    double: MeetingOrchestrator = mock_of[MeetingOrchestrator](
        run_meeting=AsyncMock(
            spec=MeetingOrchestrator.run_meeting, return_value=record
        ),
    )
    return double


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


def _sprint_start_ceremony() -> SprintCeremonyConfig:
    return SprintCeremonyConfig(
        name="kickoff",
        protocol=MeetingProtocolType.ROUND_ROBIN,
        policy_override=CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.TASK_DRIVEN,
            strategy_config={"trigger": "sprint_start"},
        ),
    )


class TestSprintStartFiresThroughRealComponents:
    """Registration has to precede the one-shot it exists to feed."""

    async def test_activation_runs_the_sprint_start_meeting(self) -> None:
        """No manual trigger_event: activation alone must reach a meeting.

        Ordering is the whole invariant, and a doubled meeting scheduler
        would answer the trigger whether or not registration ran first.
        """
        orchestrator = _orchestrator()
        meeting_scheduler = _meeting_scheduler(orchestrator)
        scheduler = CeremonyScheduler(meeting_scheduler=meeting_scheduler)

        await scheduler.activate_sprint(
            _sprint(),
            _sprint_config((_sprint_start_ceremony(),)),
            TaskDrivenStrategy(),
        )

        _run_meeting(orchestrator).assert_awaited_once()


class TestAMeetingThatNeverRanIsNotReportedAsFired:
    """The scheduler reports a failed meeting by returning no record.

    It does not raise, so treating a clean return as "fired" would retire
    a one-shot that never happened: the ceremony is durably marked done
    and, being one-shot, can never run for that sprint again.
    """

    async def test_no_record_reports_the_ceremony_un_fired(self) -> None:
        orchestrator = _orchestrator()
        _run_meeting(orchestrator).side_effect = MeetingProtocolNotFoundError(
            "registry empty"
        )
        meeting_scheduler = _meeting_scheduler(orchestrator)
        scheduler = CeremonyScheduler(meeting_scheduler=meeting_scheduler)

        with capture_logs() as logs:
            await scheduler.activate_sprint(
                _sprint(),
                _sprint_config((_sprint_start_ceremony(),)),
                TaskDrivenStrategy(),
            )

        _run_meeting(orchestrator).assert_awaited_once()
        assert [
            entry
            for entry in logs
            if entry.get("event") == SPRINT_CEREMONY_TRIGGER_FAILED
            and "stays eligible" in str(entry.get("note", ""))
        ]

    async def test_the_sprint_still_activates(self) -> None:
        """A ceremony that could not run is not an activation failure."""
        orchestrator = _orchestrator()
        _run_meeting(orchestrator).side_effect = MeetingProtocolNotFoundError(
            "registry empty"
        )
        meeting_scheduler = _meeting_scheduler(orchestrator)
        scheduler = CeremonyScheduler(meeting_scheduler=meeting_scheduler)

        await scheduler.activate_sprint(
            _sprint(),
            _sprint_config((_sprint_start_ceremony(),)),
            TaskDrivenStrategy(),
        )

        assert scheduler.running is True


class TestADeactivatedSprintDoesNotRunMeetings:
    """Ceremonies fire outside the lock, so the sprint can end mid-flight."""

    async def test_a_ceremony_for_an_ended_sprint_is_skipped(self) -> None:
        orchestrator = _orchestrator()
        meeting_scheduler = _meeting_scheduler(orchestrator)
        scheduler = CeremonyScheduler(meeting_scheduler=meeting_scheduler)
        await scheduler.activate_sprint(
            _sprint(),
            _sprint_config((_planning_ceremony(),)),
            TaskDrivenStrategy(),
        )
        await scheduler.deactivate_sprint()

        fired = await scheduler._trigger_ceremony("sprint_planning", _sprint())

        assert fired is False
        _run_meeting(orchestrator).assert_not_awaited()


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

        run_meeting = _run_meeting(orchestrator)
        run_meeting.assert_awaited_once()
        await_args = run_meeting.await_args
        assert await_args is not None
        config = await_args.kwargs["protocol_config"]
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
        assert protocol.config.max_discussion_tokens == 2000
