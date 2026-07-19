"""Load-bearing integration tests: an em-dash cannot cross an output boundary.

The em-dash is built at runtime (``chr(0x2014)``) so no literal U+2014 lands in
committed test source. Each test proves the hard ban blocks or reworks the
output before it can be emitted or completed.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.enums import MessagePriority, MessageType
from synthorg.communication.message import Message, TextPart
from synthorg.communication.messages.service import MessageService
from synthorg.communication.messenger import AgentMessenger, _guard_outbound
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.redteam_review_input import RedTeamReviewInput
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.engine._review_oracle_gates import apply_output_policy_gate
from synthorg.engine.output_style.errors import OutputPolicyViolationError
from synthorg.engine.output_style.models import OutputStyleConfig
from synthorg.engine.output_style.service import (
    OutputStylePolicyService,
    current_output_policy_service,
    set_output_policy_service,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.tools.git_tools import GitCommitTool
from tests._shared import mock_of

_EM_DASH = chr(0x2014)


@pytest.fixture
def _wired_service() -> Iterator[None]:
    previous = current_output_policy_service()
    set_output_policy_service(OutputStylePolicyService.from_config(OutputStyleConfig()))
    try:
        yield
    finally:
        set_output_policy_service(previous)


def _task() -> Task:
    return Task(
        title="Write a status report",
        description="Summarise progress.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-x",
        created_by="manager",
    )


def _deliverable(content: str) -> RedTeamReviewInput:
    return RedTeamReviewInput(
        task_id="task-1",
        execution_id="exec-1",
        deliverable_content=content,
        acceptance_criteria=("A phased rollout.",),
        assigned_agent_id="agent-1",
        autonomy=AutonomyLevel.SEMI,
        project_id="proj-x",
    )


def _message(text: str) -> Message:
    return Message(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        sender="agent-1",
        to="#eng",
        type=MessageType.STATUS_REPORT,
        priority=MessagePriority.NORMAL,
        channel="#eng",
        parts=(TextPart(text=text),),
    )


@pytest.mark.usefixtures("_wired_service")
class TestMessageGuard:
    @pytest.mark.unit
    def test_guard_blocks_emdash(self) -> None:
        with pytest.raises(OutputPolicyViolationError):
            _guard_outbound(_message(f"shipping {_EM_DASH} done"))

    @pytest.mark.unit
    def test_guard_passes_clean(self) -> None:
        message = _message("shipping: done")
        assert _guard_outbound(message) is message


@pytest.mark.usefixtures("_wired_service")
class TestMessengerBoundary:
    @pytest.mark.unit
    async def test_send_message_blocks_emdash(self) -> None:
        bus = mock_of[MessageBus]()
        messenger = AgentMessenger("agent-1", "Agent One", bus)
        with pytest.raises(OutputPolicyViolationError):
            await messenger.send_message(
                to="team",
                channel="#eng",
                content=f"shipping the parser {_EM_DASH} done",
                message_type=MessageType.STATUS_REPORT,
            )
        bus.publish.assert_not_awaited()

    @pytest.mark.unit
    async def test_broadcast_blocks_emdash(self) -> None:
        bus = mock_of[MessageBus]()
        messenger = AgentMessenger("agent-1", "Agent One", bus)
        with pytest.raises(OutputPolicyViolationError):
            await messenger.broadcast(
                content=f"all hands {_EM_DASH} update",
                message_type=MessageType.ANNOUNCEMENT,
            )
        bus.publish.assert_not_awaited()

    @pytest.mark.unit
    async def test_clean_message_publishes(self) -> None:
        bus = mock_of[MessageBus]()
        messenger = AgentMessenger("agent-1", "Agent One", bus)
        await messenger.send_message(
            to="team",
            channel="#eng",
            content="shipping the parser: done",
            message_type=MessageType.STATUS_REPORT,
        )
        bus.publish.assert_awaited_once()

    @pytest.mark.unit
    async def test_mcp_send_blocks_emdash(self) -> None:
        bus = mock_of[MessageBus]()
        service = MessageService(bus=bus, persistence=mock_of[PersistenceBackend]())
        with pytest.raises(OutputPolicyViolationError):
            await service.send_message(
                message=_message(f"done {_EM_DASH} shipped"), actor_id="agent-1"
            )
        bus.publish.assert_not_awaited()


@pytest.mark.usefixtures("_wired_service")
class TestCommitBoundary:
    @pytest.mark.unit
    async def test_commit_message_emdash_errors(self, tmp_path: Path) -> None:
        tool = GitCommitTool(workspace=tmp_path)
        result = await tool.execute(
            arguments={"message": f"fix: tidy parser {_EM_DASH} again"}
        )
        assert result.is_error is True
        assert "Em-dash" in result.content


class TestDeliverableGate:
    @pytest.mark.unit
    @pytest.mark.usefixtures("_wired_service")
    def test_emdash_deliverable_reworked(self) -> None:
        target, reason, _event, approved = apply_output_policy_gate(
            deliverable=_deliverable(
                f"The rollout plan {_EM_DASH} phase one ships first."
            ),
            task=_task(),
            target=TaskStatus.COMPLETED,
            transition_reason="ok",
            event="evt",
            approved=True,
        )
        assert approved is False
        assert target is TaskStatus.IN_PROGRESS
        assert "Output-style policy" in reason

    @pytest.mark.unit
    @pytest.mark.usefixtures("_wired_service")
    def test_clean_deliverable_completes(self) -> None:
        target, _reason, _event, approved = apply_output_policy_gate(
            deliverable=_deliverable("The rollout plan: phase one ships first."),
            task=_task(),
            target=TaskStatus.COMPLETED,
            transition_reason="ok",
            event="evt",
            approved=True,
        )
        assert approved is True
        assert target is TaskStatus.COMPLETED

    @pytest.mark.unit
    def test_gate_passes_through_when_unwired(self) -> None:
        set_output_policy_service(None)
        _target, _reason, _event, approved = apply_output_policy_gate(
            deliverable=_deliverable(f"unguarded {_EM_DASH} deliverable"),
            task=_task(),
            target=TaskStatus.COMPLETED,
            transition_reason="ok",
            event="evt",
            approved=True,
        )
        assert approved is True
