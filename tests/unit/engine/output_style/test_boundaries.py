"""Load-bearing integration tests: an em-dash cannot cross an output boundary.

The em-dash is built at runtime (``chr(0x2014)``) so no literal U+2014 lands in
committed test source. Each test proves the hard ban blocks or reworks the
output before it can be emitted or completed, at every guarded boundary
(messages, commits, deliverables, code files, issue/PR bodies) and exercises the
shadow and auto-rewrite modes at a boundary, not just at the evaluator.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthorg.communication._output_guard import guard_message_output
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.enums import MessagePriority, MessageType
from synthorg.communication.message import Message, TextPart
from synthorg.communication.messages.service import MessageService
from synthorg.communication.messenger import AgentMessenger
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.redteam_review_input import RedTeamReviewInput
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.engine._review_oracle_gates import apply_output_policy_gate
from synthorg.engine.output_style.errors import OutputPolicyViolationError
from synthorg.engine.output_style.models import (
    EnforcementMode,
    OutputStyleConfig,
    OutputStyleRule,
    RulePack,
    RuleType,
)
from synthorg.engine.output_style.service import (
    OutputStylePolicyService,
    current_output_policy_service,
    set_output_policy_service,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.tools.file_system.edit_file import EditFileTool
from synthorg.tools.file_system.write_file import WriteFileTool
from synthorg.tools.forge.forge_tools import _guard_forge_text
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


def _wire(rule: OutputStyleRule, *, shadow_mode: bool = False) -> None:
    """Bind a service whose only rule is *rule* (for mode-at-boundary tests)."""
    pack = RulePack(name="test", version="1", rules=(rule,))
    set_output_policy_service(
        OutputStylePolicyService(
            pack=pack, config=OutputStyleConfig(shadow_mode=shadow_mode)
        )
    )


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
        agent_summary=content,
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
            guard_message_output(
                _message(f"shipping {_EM_DASH} done"), agent_id="agent-1"
            )

    @pytest.mark.unit
    def test_guard_passes_clean(self) -> None:
        message = _message("shipping: done")
        assert guard_message_output(message, agent_id="agent-1") is message

    @pytest.mark.unit
    def test_multi_part_blocks_on_later_part(self) -> None:
        message = Message(
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            sender="agent-1",
            to="#eng",
            type=MessageType.STATUS_REPORT,
            priority=MessagePriority.NORMAL,
            channel="#eng",
            parts=(
                TextPart(text="clean first part"),
                TextPart(text=f"second part {_EM_DASH} bad"),
            ),
        )
        with pytest.raises(OutputPolicyViolationError):
            guard_message_output(message, agent_id="agent-1")


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
    async def test_send_direct_blocks_emdash(self) -> None:
        bus = mock_of[MessageBus]()
        messenger = AgentMessenger("agent-1", "Agent One", bus)
        with pytest.raises(OutputPolicyViolationError):
            await messenger.send_direct(
                to="agent-2",
                content=f"psst {_EM_DASH} secret",
                message_type=MessageType.STATUS_REPORT,
            )
        bus.send_direct.assert_not_awaited()

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


class TestModesAtBoundary:
    @pytest.mark.unit
    async def test_shadow_rule_publishes(self) -> None:
        _wire(
            OutputStyleRule(
                id="shadow_word",
                type=RuleType.LITERAL_BAN,
                patterns=("badword",),
                message="no badword",
                mode=EnforcementMode.SHADOW,
            )
        )
        bus = mock_of[MessageBus]()
        messenger = AgentMessenger("agent-1", "Agent One", bus)
        await messenger.send_message(
            to="team",
            channel="#eng",
            content="this has badword in it",
            message_type=MessageType.STATUS_REPORT,
        )
        bus.publish.assert_awaited_once()

    @pytest.mark.unit
    async def test_auto_rewrite_rewrites_before_publish(self) -> None:
        _wire(
            OutputStyleRule(
                id="rw",
                type=RuleType.LITERAL_BAN,
                patterns=("badword",),
                message="no badword",
                mode=EnforcementMode.AUTO_REWRITE,
                rewrite="okword",
            )
        )
        bus = mock_of[MessageBus]()
        messenger = AgentMessenger("agent-1", "Agent One", bus)
        await messenger.send_message(
            to="team",
            channel="#eng",
            content="this has badword in it",
            message_type=MessageType.STATUS_REPORT,
        )
        bus.publish.assert_awaited_once()
        published = bus.publish.await_args.args[0]
        assert "okword" in published.parts[0].text
        assert "badword" not in published.parts[0].text


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


@pytest.mark.usefixtures("_wired_service")
class TestCodeFileBoundary:
    @pytest.mark.unit
    async def test_write_file_blocks_emdash(self, tmp_path: Path) -> None:
        tool = WriteFileTool(workspace_root=tmp_path)
        result = await tool.execute(
            arguments={"path": "mod.py", "content": f"# tidy {_EM_DASH} code\n"}
        )
        assert result.is_error is True
        assert "Em-dash" in result.content
        assert not (tmp_path / "mod.py").exists()

    @pytest.mark.unit
    async def test_write_file_clean_writes(self, tmp_path: Path) -> None:
        tool = WriteFileTool(workspace_root=tmp_path)
        result = await tool.execute(
            arguments={"path": "mod.py", "content": "# tidy: code\n"}
        )
        assert result.is_error is False
        assert (tmp_path / "mod.py").exists()

    @pytest.mark.unit
    async def test_edit_file_blocks_emdash_replacement(self, tmp_path: Path) -> None:
        target = tmp_path / "mod.py"
        target.write_text("x = 1\n", encoding="utf-8")
        tool = EditFileTool(workspace_root=tmp_path)
        result = await tool.execute(
            arguments={
                "path": "mod.py",
                "old_text": "x = 1",
                "new_text": f"x = 1  # done {_EM_DASH} yes",
            }
        )
        assert result.is_error is True
        assert target.read_text(encoding="utf-8") == "x = 1\n"


@pytest.mark.usefixtures("_wired_service")
class TestForgeBodyBoundary:
    @pytest.mark.unit
    def test_forge_body_blocks_emdash(self) -> None:
        guard = _guard_forge_text(title="Add feature", body=f"why {_EM_DASH} because")
        assert guard.error is not None
        assert guard.error.is_error is True

    @pytest.mark.unit
    def test_forge_title_blocks_emdash(self) -> None:
        guard = _guard_forge_text(title=f"Add {_EM_DASH} feature", body="clean body")
        assert guard.error is not None

    @pytest.mark.unit
    def test_forge_clean_passes(self) -> None:
        guard = _guard_forge_text(title="Add feature", body="clean body")
        assert guard.error is None
        assert guard.title == "Add feature"
        assert guard.body == "clean body"

    @pytest.mark.unit
    def test_forge_commit_title_blocks_emdash_on_commit_channel(self) -> None:
        # A merge commit title is a COMMIT_MESSAGE (code) boundary, reject-only.
        guard = _guard_forge_text(
            title=f"merge {_EM_DASH} done", body="", is_commit=True
        )
        assert guard.error is not None
        assert guard.error.is_error is True


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
