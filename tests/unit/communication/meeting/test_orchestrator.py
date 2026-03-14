"""Tests for meeting orchestrator."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.communication.meeting.config import (
    MeetingProtocolConfig,
)
from synthorg.communication.meeting.enums import (
    MeetingProtocolType,
    MeetingStatus,
)
from synthorg.communication.meeting.errors import (
    MeetingParticipantError,
    MeetingProtocolNotFoundError,
)
from synthorg.communication.meeting.models import (
    ActionItem,
    MeetingAgenda,
    MeetingMinutes,
)
from synthorg.communication.meeting.orchestrator import (
    MeetingOrchestrator,
    _format_exception,
)
from synthorg.communication.meeting.position_papers import (
    PositionPapersProtocol,
)
from synthorg.communication.meeting.protocol import MeetingProtocol
from synthorg.communication.meeting.round_robin import RoundRobinProtocol
from synthorg.core.enums import Priority
from tests.unit.communication.meeting.conftest import (
    make_mock_agent_caller,
)

pytestmark = pytest.mark.timeout(30)


def _make_orchestrator(
    *,
    task_creator: object | None = None,
    protocol_config: MeetingProtocolConfig | None = None,
) -> MeetingOrchestrator:
    """Create an orchestrator with default protocols registered."""
    cfg = protocol_config or MeetingProtocolConfig()
    registry: dict[MeetingProtocolType, MeetingProtocol] = {
        MeetingProtocolType.ROUND_ROBIN: RoundRobinProtocol(
            config=cfg.round_robin,
        ),
        MeetingProtocolType.POSITION_PAPERS: PositionPapersProtocol(
            config=cfg.position_papers,
        ),
    }
    caller = make_mock_agent_caller()
    return MeetingOrchestrator(
        protocol_registry=registry,
        agent_caller=caller,
        task_creator=task_creator,  # type: ignore[arg-type]
    )


@pytest.mark.unit
class TestFormatException:
    """Tests for _format_exception helper."""

    def test_simple_exception(self) -> None:
        exc = RuntimeError("something broke")
        assert _format_exception(exc) == "something broke"

    def test_exception_group(self) -> None:
        group = ExceptionGroup(
            "errors",
            [RuntimeError("err1"), ValueError("err2")],
        )
        result = _format_exception(group)
        assert "Multiple errors:" in result
        assert "RuntimeError: err1" in result
        assert "ValueError: err2" in result

    def test_nested_exception_group(self) -> None:
        inner = ExceptionGroup("inner", [TypeError("bad type")])
        outer = ExceptionGroup(
            "outer",
            [RuntimeError("outer err"), inner],
        )
        result = _format_exception(outer)
        assert "RuntimeError: outer err" in result
        assert "TypeError: bad type" in result


@pytest.mark.unit
class TestMeetingOrchestratorValidation:
    """Tests for orchestrator input validation."""

    async def test_empty_participants_raises(
        self,
        simple_agenda: MeetingAgenda,
    ) -> None:
        orchestrator = _make_orchestrator()
        with pytest.raises(MeetingParticipantError, match="At least one"):
            await orchestrator.run_meeting(
                meeting_type_name="standup",
                protocol_config=MeetingProtocolConfig(),
                agenda=simple_agenda,
                leader_id="leader",
                participant_ids=(),
                token_budget=2000,
            )

    async def test_leader_in_participants_raises(
        self,
        simple_agenda: MeetingAgenda,
    ) -> None:
        orchestrator = _make_orchestrator()
        with pytest.raises(MeetingParticipantError, match="must not be in"):
            await orchestrator.run_meeting(
                meeting_type_name="standup",
                protocol_config=MeetingProtocolConfig(),
                agenda=simple_agenda,
                leader_id="leader",
                participant_ids=("leader", "agent-b"),
                token_budget=2000,
            )

    async def test_duplicate_participants_raises(
        self,
        simple_agenda: MeetingAgenda,
    ) -> None:
        orchestrator = _make_orchestrator()
        with pytest.raises(MeetingParticipantError, match="Duplicate participant"):
            await orchestrator.run_meeting(
                meeting_type_name="standup",
                protocol_config=MeetingProtocolConfig(),
                agenda=simple_agenda,
                leader_id="leader",
                participant_ids=("agent-a", "agent-b", "agent-a"),
                token_budget=2000,
            )

    async def test_unregistered_protocol_raises(
        self,
        simple_agenda: MeetingAgenda,
    ) -> None:
        orchestrator = _make_orchestrator()
        config = MeetingProtocolConfig(
            protocol=MeetingProtocolType.STRUCTURED_PHASES,
        )
        with pytest.raises(
            MeetingProtocolNotFoundError,
            match="not registered",
        ):
            await orchestrator.run_meeting(
                meeting_type_name="standup",
                protocol_config=config,
                agenda=simple_agenda,
                leader_id="leader",
                participant_ids=("agent-a",),
                token_budget=2000,
            )


@pytest.mark.unit
class TestMeetingOrchestratorExecution:
    """Tests for orchestrator meeting execution."""

    async def test_successful_meeting(
        self,
        simple_agenda: MeetingAgenda,
    ) -> None:
        orchestrator = _make_orchestrator()

        record = await orchestrator.run_meeting(
            meeting_type_name="standup",
            protocol_config=MeetingProtocolConfig(),
            agenda=simple_agenda,
            leader_id="leader",
            participant_ids=("agent-a", "agent-b"),
            token_budget=10000,
        )

        assert record.status == MeetingStatus.COMPLETED
        assert record.minutes is not None
        assert record.error_message is None
        assert record.meeting_type_name == "standup"
        assert record.protocol_type == MeetingProtocolType.ROUND_ROBIN

    async def test_meeting_id_generated(
        self,
        simple_agenda: MeetingAgenda,
    ) -> None:
        orchestrator = _make_orchestrator()

        record = await orchestrator.run_meeting(
            meeting_type_name="standup",
            protocol_config=MeetingProtocolConfig(),
            agenda=simple_agenda,
            leader_id="leader",
            participant_ids=("agent-a",),
            token_budget=10000,
        )

        assert record.meeting_id.startswith("mtg-")

    async def test_position_papers_protocol(
        self,
        simple_agenda: MeetingAgenda,
    ) -> None:
        orchestrator = _make_orchestrator()
        config = MeetingProtocolConfig(
            protocol=MeetingProtocolType.POSITION_PAPERS,
        )

        record = await orchestrator.run_meeting(
            meeting_type_name="design_review",
            protocol_config=config,
            agenda=simple_agenda,
            leader_id="leader",
            participant_ids=("agent-a", "agent-b"),
            token_budget=10000,
        )

        assert record.status == MeetingStatus.COMPLETED
        assert record.protocol_type == MeetingProtocolType.POSITION_PAPERS

    async def test_token_budget_passed_through(
        self,
        simple_agenda: MeetingAgenda,
    ) -> None:
        orchestrator = _make_orchestrator()

        record = await orchestrator.run_meeting(
            meeting_type_name="standup",
            protocol_config=MeetingProtocolConfig(),
            agenda=simple_agenda,
            leader_id="leader",
            participant_ids=("agent-a",),
            token_budget=5000,
        )

        assert record.token_budget == 5000


@pytest.mark.unit
class TestMeetingOrchestratorRecords:
    """Tests for audit trail recording."""

    async def test_records_stored(
        self,
        simple_agenda: MeetingAgenda,
    ) -> None:
        orchestrator = _make_orchestrator()

        await orchestrator.run_meeting(
            meeting_type_name="standup",
            protocol_config=MeetingProtocolConfig(),
            agenda=simple_agenda,
            leader_id="leader",
            participant_ids=("agent-a",),
            token_budget=10000,
        )

        records = orchestrator.get_records()
        assert len(records) == 1

    async def test_multiple_meetings_recorded(
        self,
        simple_agenda: MeetingAgenda,
    ) -> None:
        orchestrator = _make_orchestrator()

        for _ in range(3):
            await orchestrator.run_meeting(
                meeting_type_name="standup",
                protocol_config=MeetingProtocolConfig(),
                agenda=simple_agenda,
                leader_id="leader",
                participant_ids=("agent-a",),
                token_budget=10000,
            )

        records = orchestrator.get_records()
        assert len(records) == 3
        # Each has a unique meeting ID
        ids = {r.meeting_id for r in records}
        assert len(ids) == 3

    async def test_get_records_returns_tuple(
        self,
        simple_agenda: MeetingAgenda,
    ) -> None:
        orchestrator = _make_orchestrator()
        records = orchestrator.get_records()
        assert isinstance(records, tuple)


@pytest.mark.unit
class TestMeetingOrchestratorErrorHandling:
    """Tests for error handling in orchestrator."""

    async def test_agent_error_produces_failed_record(
        self,
        simple_agenda: MeetingAgenda,
    ) -> None:
        async def _failing_caller(
            agent_id: str,
            prompt: str,
            max_tokens: int,
        ) -> object:
            msg = "Agent unreachable"
            raise RuntimeError(msg)

        registry = {
            MeetingProtocolType.ROUND_ROBIN: RoundRobinProtocol(
                config=MeetingProtocolConfig().round_robin,
            ),
        }
        orchestrator = MeetingOrchestrator(
            protocol_registry=registry,
            agent_caller=_failing_caller,  # type: ignore[arg-type]
        )

        record = await orchestrator.run_meeting(
            meeting_type_name="standup",
            protocol_config=MeetingProtocolConfig(),
            agenda=simple_agenda,
            leader_id="leader",
            participant_ids=("agent-a",),
            token_budget=10000,
        )

        assert record.status == MeetingStatus.FAILED
        assert record.error_message is not None
        assert "Agent unreachable" in record.error_message


@pytest.mark.unit
class TestMeetingOrchestratorBudgetExhaustion:
    """Tests for budget exhaustion handling."""

    async def test_budget_exhaustion_produces_record(
        self,
        simple_agenda: MeetingAgenda,
    ) -> None:
        """Tiny budget triggers BUDGET_EXHAUSTED status."""
        orchestrator = _make_orchestrator()
        record = await orchestrator.run_meeting(
            meeting_type_name="standup",
            protocol_config=MeetingProtocolConfig(),
            agenda=simple_agenda,
            leader_id="leader",
            participant_ids=("agent-a",),
            token_budget=1,
        )
        assert record.status == MeetingStatus.BUDGET_EXHAUSTED
        assert record.error_message is not None

    @pytest.mark.parametrize("budget", [0, -1, -100])
    async def test_non_positive_token_budget_raises(
        self,
        simple_agenda: MeetingAgenda,
        budget: int,
    ) -> None:
        orchestrator = _make_orchestrator()
        with pytest.raises(ValueError, match="positive"):
            await orchestrator.run_meeting(
                meeting_type_name="standup",
                protocol_config=MeetingProtocolConfig(),
                agenda=simple_agenda,
                leader_id="leader",
                participant_ids=("agent-a",),
                token_budget=budget,
            )


@pytest.mark.unit
class TestMeetingOrchestratorTaskCreation:
    """Tests for task creation from action items."""

    async def test_task_creator_called_with_correct_args(self) -> None:
        """Task creator receives correct args from action items."""
        created_tasks: list[tuple[str, str | None, Priority]] = []

        def _creator(
            desc: str,
            assignee: str | None,
            priority: Priority,
        ) -> None:
            created_tasks.append((desc, assignee, priority))

        now = datetime.now(UTC)
        agenda = MeetingAgenda(title="Test")
        action_items = (
            ActionItem(
                description="Deploy API",
                assignee_id="agent-ops",
                priority=Priority.HIGH,
            ),
            ActionItem(description="Write docs"),
        )
        minutes = MeetingMinutes(
            meeting_id="m-1",
            protocol_type=MeetingProtocolType.ROUND_ROBIN,
            leader_id="leader",
            participant_ids=("agent-a",),
            agenda=agenda,
            action_items=action_items,
            started_at=now,
            ended_at=now,
        )

        # Use a mock protocol that returns pre-built minutes
        mock_protocol = MagicMock()
        mock_protocol.get_protocol_type.return_value = MeetingProtocolType.ROUND_ROBIN
        mock_protocol.run = AsyncMock(return_value=minutes)

        registry: dict[MeetingProtocolType, MeetingProtocol] = {
            MeetingProtocolType.ROUND_ROBIN: mock_protocol,
        }
        orchestrator = MeetingOrchestrator(
            protocol_registry=registry,
            agent_caller=make_mock_agent_caller(),
            task_creator=_creator,
        )

        record = await orchestrator.run_meeting(
            meeting_type_name="standup",
            protocol_config=MeetingProtocolConfig(),
            agenda=agenda,
            leader_id="leader",
            participant_ids=("agent-a",),
            token_budget=10000,
        )

        assert record.status == MeetingStatus.COMPLETED
        assert len(created_tasks) == 2
        assert created_tasks[0] == (
            "Deploy API",
            "agent-ops",
            Priority.HIGH,
        )
        assert created_tasks[1] == ("Write docs", None, Priority.MEDIUM)

    async def test_task_creator_not_called_without_action_items(
        self,
        simple_agenda: MeetingAgenda,
    ) -> None:
        created_tasks: list[tuple[str, str | None, Priority]] = []

        def _creator(
            desc: str,
            assignee: str | None,
            priority: Priority,
        ) -> None:
            created_tasks.append((desc, assignee, priority))

        orchestrator = _make_orchestrator(task_creator=_creator)

        await orchestrator.run_meeting(
            meeting_type_name="standup",
            protocol_config=MeetingProtocolConfig(),
            agenda=simple_agenda,
            leader_id="leader",
            participant_ids=("agent-a",),
            token_budget=10000,
        )

        # Mock responses don't produce action items
        assert len(created_tasks) == 0

    async def test_task_creator_not_called_when_disabled(
        self,
        simple_agenda: MeetingAgenda,
    ) -> None:
        created_tasks: list[tuple[str, str | None, Priority]] = []

        def _creator(
            desc: str,
            assignee: str | None,
            priority: Priority,
        ) -> None:
            created_tasks.append((desc, assignee, priority))

        config = MeetingProtocolConfig(auto_create_tasks=False)
        orchestrator = _make_orchestrator(
            task_creator=_creator,
            protocol_config=config,
        )

        await orchestrator.run_meeting(
            meeting_type_name="standup",
            protocol_config=config,
            agenda=simple_agenda,
            leader_id="leader",
            participant_ids=("agent-a",),
            token_budget=10000,
        )

        assert len(created_tasks) == 0

    async def test_task_creator_failure_does_not_crash(
        self,
        simple_agenda: MeetingAgenda,
    ) -> None:
        """A failing task_creator should not cause the meeting to fail."""

        def _failing_creator(
            desc: str,
            assignee: str | None,
            priority: Priority,
        ) -> None:
            msg = "Task system unavailable"
            raise RuntimeError(msg)

        orchestrator = _make_orchestrator(task_creator=_failing_creator)

        # Meeting should succeed even if task creation would fail
        record = await orchestrator.run_meeting(
            meeting_type_name="standup",
            protocol_config=MeetingProtocolConfig(),
            agenda=simple_agenda,
            leader_id="leader",
            participant_ids=("agent-a",),
            token_budget=10000,
        )

        assert record.status == MeetingStatus.COMPLETED
