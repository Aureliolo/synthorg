"""Unit tests for ShellCommandTool."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Final

import pytest

from synthorg.core.execution_identity import (
    ExecutionIdentity,
    execution_identity_scope,
    run_identity_scope,
)
from synthorg.core.shell_semantics import RECORDED_RUN_RULE
from synthorg.core.types import NotBlankStr
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionPurpose,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.sandbox.errors import SandboxError, SandboxShuttingDownError
from synthorg.tools.sandbox.result import SandboxResult
from synthorg.tools.terminal.config import TerminalConfig
from synthorg.tools.terminal.shell_command import ShellCommandTool
from tests._shared import FakeClock, FakeSandbox
from tests.unit.deliverable_receipts._fakes import RecordingCodeExecutionStore

#: A time no wall clock will return, so a record carrying it can only have
#: come from the injected seam.
_STAMPED_AT: Final[datetime] = datetime(2019, 3, 14, 15, 9, 26, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _scoped_run() -> Iterator[None]:
    """Run every case inside a scoped run, as the tool now requires.

    The tool refuses to reach the sandbox without a project, because an absent
    project selects the whole workspace and with it every other project's
    files. These cases are about what it does once scoped; the refusal itself
    is covered in ``test_workspace_scope_agreement``.

    Yields:
        Nothing; the scope is torn down on the way out.
    """
    with run_identity_scope(
        execution_id="exec-1", task_id="task-1", project_id="proj-1"
    ):
        yield


class TestTheRuleIsSaidUpFront:
    """The shape a recorded run takes reaches the agent before it types one.

    The summary is all an agent sees of a tool it has not loaded, and the
    summary is capped, so the rule has to fit inside the cap or it is told
    only by the refusal, one rework round too late.
    """

    @pytest.mark.unit
    def test_the_summary_carries_the_recorded_run_rule_whole(self) -> None:
        tool = ShellCommandTool(sandbox=FakeSandbox())

        assert tool.to_l1_metadata().short_description.endswith(RECORDED_RUN_RULE)


class TestTestRunCaptureWiring:
    """The classifier is well covered; what it is wired into was not.

    A wrong command variable, a forgotten repository or a dropped clock
    would silently disarm the build/test oracle while every classifier
    test stayed green, because none of them touches the tool.
    """

    @pytest.mark.unit
    async def test_a_suite_run_through_the_shell_leaves_a_receipt(self) -> None:
        store = RecordingCodeExecutionStore()
        clock = FakeClock(start=_STAMPED_AT)
        tool = ShellCommandTool(
            sandbox=FakeSandbox(),
            code_execution_records=store.repository,
            clock=clock,
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
        # The class docstring names a dropped clock as a failure mode this
        # suite must catch; without this a tool that ignored the seam and
        # read wall-clock time would keep every assertion above green.
        assert record.executed_at == _STAMPED_AT

    @pytest.mark.unit
    async def test_a_non_suite_command_leaves_none(self) -> None:
        store = RecordingCodeExecutionStore()
        tool = ShellCommandTool(
            sandbox=FakeSandbox(),
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
    async def test_the_tools_category_reaches_the_sandbox(self) -> None:
        """The sandbox resolves the runtime and the mount mode from it alone.

        A shell command writes to the workspace by definition, so dropping the
        category left the mount read-only and every build step reporting a
        read-only filesystem.
        """
        sandbox = FakeSandbox(SandboxResult(stdout="ok", stderr="", returncode=0))
        tool = ShellCommandTool(sandbox=sandbox)

        await tool.execute(arguments={"command": "make build"})

        assert sandbox.last_call is not None
        assert sandbox.last_call.category == ToolCategory.TERMINAL.value

    @pytest.mark.unit
    async def test_failed_command(self) -> None:
        sandbox = FakeSandbox(
            SandboxResult(stdout="", stderr="not found", returncode=127)
        )
        tool = ShellCommandTool(sandbox=sandbox)
        result = await tool.execute(arguments={"command": "badcmd"})
        assert result.is_error is True
        assert "not found" in result.content
        assert result.metadata["returncode"] == 127

    @pytest.mark.unit
    async def test_timeout(self) -> None:
        sandbox = FakeSandbox(
            SandboxResult(stdout="", stderr="", returncode=-1, timed_out=True)
        )
        tool = ShellCommandTool(sandbox=sandbox)
        result = await tool.execute(arguments={"command": "sleep 999", "timeout": 1.0})
        assert result.is_error is True
        assert "timed out" in result.content.lower()

    @pytest.mark.unit
    async def test_sandbox_error(self) -> None:
        sandbox = FakeSandbox(error=SandboxError("container crashed"))
        tool = ShellCommandTool(sandbox=sandbox)
        result = await tool.execute(arguments={"command": "echo hi"})
        assert result.is_error is True
        assert "sandbox" in result.content.lower()

    @pytest.mark.unit
    async def test_a_terminal_sandbox_condition_is_raised_not_returned(
        self,
    ) -> None:
        """The agent is not offered a retry it can only lose money on.

        A shut-down backend refuses every later command too, so returning this
        as a result puts the agent in a loop it cannot leave: measured on a
        recorded sweep, six units each spent a 1.5-million-token ceiling
        retrying ``ls`` and wrote nothing at all.
        """
        sandbox = FakeSandbox(error=SandboxShuttingDownError("tearing down"))
        tool = ShellCommandTool(sandbox=sandbox)
        with pytest.raises(SandboxShuttingDownError):
            await tool.execute(arguments={"command": "echo hi"})

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


class TestNoSandboxWired:
    """What an agent is told when this deployment cannot execute at all.

    The tool stays registered without a sandbox so it can answer for the
    deployment rather than going missing. That answer is the whole point, so
    it is asserted rather than assumed: silently returning a bare failure here
    is the shape that reads as the agent's mistake.
    """

    @pytest.mark.unit
    async def test_it_refuses_rather_than_executing(self) -> None:
        tool = ShellCommandTool(sandbox=None)

        result = await tool.execute(arguments={"command": "echo hi"})

        assert result.is_error is True
        assert "agent_tool_execution" in result.content
        assert "Nothing about the command caused this" in result.content

    @pytest.mark.unit
    async def test_it_never_reaches_the_sandboxed_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Asserting the error alone would be the same claim the test above
        # already makes, and would still pass if the refusal branch were
        # deleted and some other path returned an error. The ordering is what
        # this pins: nothing runs in the API process, so the sandboxed path is
        # never entered at all.
        tool = ShellCommandTool(sandbox=None)
        reached: list[str] = []

        async def _record(
            command: str, *args: object, **kwargs: object
        ) -> ToolExecutionResult:
            del args, kwargs
            reached.append(command)
            return ToolExecutionResult(content="", is_error=False)

        monkeypatch.setattr(tool, "_execute_sandboxed", _record)

        result = await tool.execute(arguments={"command": "echo hi"})

        assert result.is_error is True
        assert reached == []


class TestOutputTruncation:
    """Tests for output size limiting."""

    @pytest.mark.unit
    async def test_large_output_truncated(self) -> None:
        sandbox = FakeSandbox(SandboxResult(stdout="x" * 200, stderr="", returncode=0))
        config = TerminalConfig(max_output_bytes=50)
        tool = ShellCommandTool(sandbox=sandbox, config=config)
        result = await tool.execute(arguments={"command": "big_output"})
        assert result.is_error is False
        assert "truncated" in result.content.lower()
        assert result.metadata["truncated"] is True
