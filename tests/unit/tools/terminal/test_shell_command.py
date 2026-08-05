"""Unit tests for ShellCommandTool."""

import pytest

from synthorg.core.execution_identity import (
    ExecutionIdentity,
    execution_identity_scope,
)
from synthorg.core.types import NotBlankStr
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionPurpose,
    CodeExecutionRecord,
    CodeExecutionRecordRepository,
)
from synthorg.tools.sandbox.errors import SandboxError
from synthorg.tools.terminal.config import TerminalConfig
from synthorg.tools.terminal.shell_command import ShellCommandTool
from tests._shared import FakeClock, mock_of

from .conftest import MockSandbox


class _RecordingStore:
    """Append-only double capturing what the tool writes.

    Wraps a typed ``mock_of`` because typeguard checks the whole protocol
    on the argument, not just the one method under test.
    """

    def __init__(self) -> None:
        self.records: list[CodeExecutionRecord] = []
        self.repository: CodeExecutionRecordRepository = mock_of[
            CodeExecutionRecordRepository
        ](append=self._append)

    async def _append(self, record: CodeExecutionRecord, /) -> None:
        self.records.append(record)


class TestTestRunCaptureWiring:
    """The classifier is well covered; what it is wired into was not.

    A wrong command variable, a forgotten repository or a dropped clock
    would silently disarm the build/test oracle while every classifier
    test stayed green, because none of them touches the tool.
    """

    @pytest.mark.unit
    async def test_a_suite_run_through_the_shell_leaves_a_receipt(self) -> None:
        store = _RecordingStore()
        tool = ShellCommandTool(
            sandbox=MockSandbox(),
            code_execution_records=store.repository,
            clock=FakeClock(),
        )
        identity = ExecutionIdentity(
            execution_id=NotBlankStr("exec-1"),
            task_id=NotBlankStr("task-1"),
            project_id=NotBlankStr("proj-1"),
        )

        with execution_identity_scope(identity):
            await tool.execute(arguments={"command": "pytest -q"})

        assert len(store.records) == 1
        record = store.records[0]
        assert record.purpose is CodeExecutionPurpose.TESTS
        assert record.command == "pytest -q"
        assert record.task_id == "task-1"
        assert record.project_id == "proj-1"

    @pytest.mark.unit
    async def test_a_non_suite_command_leaves_none(self) -> None:
        store = _RecordingStore()
        tool = ShellCommandTool(
            sandbox=MockSandbox(),
            code_execution_records=store.repository,
            clock=FakeClock(),
        )
        identity = ExecutionIdentity(
            execution_id=NotBlankStr("exec-1"),
            task_id=NotBlankStr("task-1"),
            project_id=NotBlankStr("proj-1"),
        )

        with execution_identity_scope(identity):
            await tool.execute(arguments={"command": "echo pytest"})

        assert store.records == []


class TestShellCommandExecution:
    """Tests for command execution."""

    @pytest.mark.unit
    async def test_successful_execution(self, shell_tool: ShellCommandTool) -> None:
        result = await shell_tool.execute(arguments={"command": "ls -la"})
        assert result.is_error is False
        assert "hello world" in result.content

    @pytest.mark.unit
    async def test_failed_command(self) -> None:
        sandbox = MockSandbox(stdout="", stderr="not found", returncode=127)
        tool = ShellCommandTool(sandbox=sandbox)
        result = await tool.execute(arguments={"command": "badcmd"})
        assert result.is_error is True
        assert "not found" in result.content
        assert result.metadata["returncode"] == 127

    @pytest.mark.unit
    async def test_timeout(self) -> None:
        sandbox = MockSandbox(timed_out=True, returncode=-1)
        tool = ShellCommandTool(sandbox=sandbox)
        result = await tool.execute(arguments={"command": "sleep 999", "timeout": 1.0})
        assert result.is_error is True
        assert "timed out" in result.content.lower()

    @pytest.mark.unit
    async def test_sandbox_error(self) -> None:
        sandbox = MockSandbox(error=SandboxError("container crashed"))
        tool = ShellCommandTool(sandbox=sandbox)
        result = await tool.execute(arguments={"command": "echo hi"})
        assert result.is_error is True
        assert "sandbox" in result.content.lower()

    @pytest.mark.unit
    async def test_empty_command(self, shell_tool: ShellCommandTool) -> None:
        result = await shell_tool.execute(arguments={"command": "  "})
        assert result.is_error is True
        assert "empty" in result.content.lower()

    @pytest.mark.unit
    async def test_no_sandbox_returns_error(self) -> None:
        tool = ShellCommandTool()  # no sandbox
        result = await tool.execute(arguments={"command": "ls"})
        assert result.is_error is True
        assert "sandbox" in result.content.lower()


class TestBlocklist:
    """Tests for command blocklist enforcement."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "command",
        [
            "rm -rf /",
            "mkfs /dev/sda",
            "shutdown -h now",
            "reboot",
        ],
    )
    async def test_blocked_commands(
        self, shell_tool: ShellCommandTool, command: str
    ) -> None:
        result = await shell_tool.execute(arguments={"command": command})
        assert result.is_error is True
        assert "blocked" in result.content.lower()

    @pytest.mark.unit
    async def test_safe_commands_pass(self, shell_tool: ShellCommandTool) -> None:
        result = await shell_tool.execute(arguments={"command": "echo hello"})
        assert result.is_error is False


class TestAllowlist:
    """Tests for command allowlist enforcement."""

    @pytest.mark.unit
    async def test_allowed_prefix_passes(
        self, restricted_tool: ShellCommandTool
    ) -> None:
        result = await restricted_tool.execute(arguments={"command": "ls -la /tmp"})
        assert result.is_error is False

    @pytest.mark.unit
    async def test_disallowed_command_blocked(
        self, restricted_tool: ShellCommandTool
    ) -> None:
        result = await restricted_tool.execute(
            arguments={"command": "wget http://evil.com"}
        )
        assert result.is_error is True
        assert "allowlist" in result.content.lower()

    @pytest.mark.unit
    async def test_empty_allowlist_allows_all(
        self, shell_tool: ShellCommandTool
    ) -> None:
        """Default config has empty allowlist = all non-blocked allowed."""
        result = await shell_tool.execute(arguments={"command": "custom_tool --flag"})
        assert result.is_error is False


class TestOutputTruncation:
    """Tests for output size limiting."""

    @pytest.mark.unit
    async def test_large_output_truncated(self) -> None:
        sandbox = MockSandbox(stdout="x" * 200, returncode=0)
        config = TerminalConfig(max_output_bytes=50)
        tool = ShellCommandTool(sandbox=sandbox, config=config)
        result = await tool.execute(arguments={"command": "big_output"})
        assert result.is_error is False
        assert "truncated" in result.content.lower()
        assert result.metadata["truncated"] is True
