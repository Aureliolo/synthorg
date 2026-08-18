"""The ceiling on one command is read live, per command.

The invariant: a command that cannot finish is not a slow command, it is a
capability the deployment does not have. A live run watched an agent time out
on ``npm install`` four times against a 30-second ceiling that no operator
surface exposed, write its tests anyway, and fail them for want of the
packages. So the number is an operator setting, and it is read when the
command runs rather than when the tool was built: an operator raising it must
not have to restart anything to learn whether the new value was enough.
"""

from collections.abc import Iterator
from typing import Any

import pytest

from synthorg.core.execution_identity import run_identity_scope
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from synthorg.tools.sandbox.result import SandboxResult
from synthorg.tools.terminal.config import TerminalConfig
from synthorg.tools.terminal.shell_command import ShellCommandTool
from tests._shared import FakeSandbox, mock_of

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _scoped_run() -> Iterator[None]:
    """Run every case inside a scoped run, as the tool requires.

    Yields:
        Nothing; the scope is torn down on the way out.
    """
    with run_identity_scope(
        execution_id="exec-1", task_id="task-1", project_id="proj-1"
    ):
        yield


def _sandbox() -> FakeSandbox:
    return FakeSandbox(SandboxResult(stdout="ok", stderr="", returncode=0))


def _resolver(values: list[float]) -> Any:  # type: ignore[explicit-any]
    """Resolver handing out a different value per read, newest last.

    Returns:
        A resolver double whose reads walk *values*.
    """
    remaining = list(values)

    async def _get_float(namespace: str, key: str) -> float:
        del namespace, key
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return mock_of[ConfigResolverProtocol](get_float=_get_float)


class TestShellCommandTimeout:
    async def test_the_resolved_setting_reaches_the_sandbox(self) -> None:
        sandbox = _sandbox()
        tool = ShellCommandTool(
            sandbox=sandbox,
            config=TerminalConfig(default_timeout=30.0),
            config_resolver=_resolver([120.0]),
        )
        await tool.execute(arguments={"command": "echo hello"})
        assert sandbox.calls[0].timeout == 120.0

    async def test_a_raised_ceiling_applies_to_the_next_command(self) -> None:
        # The whole point of reading it live: no restart, no rebuild.
        sandbox = _sandbox()
        tool = ShellCommandTool(
            sandbox=sandbox,
            config=TerminalConfig(default_timeout=30.0),
            config_resolver=_resolver([60.0, 300.0]),
        )
        await tool.execute(arguments={"command": "echo first"})
        await tool.execute(arguments={"command": "echo second"})
        assert [call.timeout for call in sandbox.calls] == [60.0, 300.0]

    async def test_the_call_s_own_timeout_still_wins(self) -> None:
        # An agent that knows its command is slow may say so, and the
        # operator's default is the value for everything that does not.
        sandbox = _sandbox()
        tool = ShellCommandTool(
            sandbox=sandbox,
            config=TerminalConfig(default_timeout=30.0),
            config_resolver=_resolver([120.0]),
        )
        await tool.execute(arguments={"command": "echo hello", "timeout": 5.0})
        assert sandbox.calls[0].timeout == 5.0

    async def test_without_a_resolver_the_configured_default_stands(self) -> None:
        # A caller with no settings backend keeps its own configuration
        # rather than a number this path invented.
        sandbox = _sandbox()
        tool = ShellCommandTool(
            sandbox=sandbox,
            config=TerminalConfig(default_timeout=45.0),
        )
        await tool.execute(arguments={"command": "echo hello"})
        assert sandbox.calls[0].timeout == 45.0

    async def test_a_failed_settings_read_does_not_fail_the_command(self) -> None:
        # Degrading to the configured default keeps agents working through a
        # settings-backend hiccup; raising here would turn every command into
        # an error.
        async def _raise(namespace: str, key: str) -> float:
            del namespace, key
            msg = "settings backend unavailable"
            raise RuntimeError(msg)

        sandbox = _sandbox()
        tool = ShellCommandTool(
            sandbox=sandbox,
            config=TerminalConfig(default_timeout=45.0),
            config_resolver=mock_of[ConfigResolverProtocol](get_float=_raise),
        )
        result = await tool.execute(arguments={"command": "echo hello"})
        assert not result.is_error
        assert sandbox.calls[0].timeout == 45.0
