"""The classifier's premise and the shell's flags are one fact.

``_test_run_capture`` accepts a piped test run because a pipeline under
``pipefail`` exits zero only when every stage did. That is a claim about how
the shell is invoked, so a tool that stopped passing the flag would leave the
classifier minting green evidence for a suite that failed, silently and with
every test still passing.
"""

from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.core.execution_identity import run_identity_scope
from synthorg.tools._shell_invocation import SHELL_ARGS_PREFIX, shell_invocation
from synthorg.tools._test_run_capture import is_test_run
from synthorg.tools.code_runner import CodeRunnerTool
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.tools.sandbox.result import SandboxResult
from synthorg.tools.terminal.shell_command import ShellCommandTool
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_PROJECT = "11111111-2222-4333-8444-555555555555"


def _sandbox() -> SandboxBackend:
    """Build a sandbox double that records the execute call.

    Returns:
        A ``SandboxBackend`` substitute whose ``execute`` is an ``AsyncMock``.
    """
    return cast(
        "SandboxBackend",
        mock_of[SandboxBackend](
            execute=AsyncMock(
                return_value=SandboxResult(stdout="", stderr="", returncode=0)
            )
        ),
    )


def _recorded_args(sandbox: SandboxBackend) -> tuple[str, ...]:
    """Return the argument tuple the tool handed the sandbox.

    Args:
        sandbox: The sandbox double the tool ran against.

    Returns:
        The recorded ``args`` keyword.
    """
    recorded = cast("AsyncMock", sandbox.execute).await_args
    assert recorded is not None
    return cast("tuple[str, ...]", recorded.kwargs["args"])


class TestPipefailIsGuaranteed:
    def test_the_invocation_sets_pipefail(self) -> None:
        program, args = shell_invocation("npm test")

        assert program == "bash"
        assert args == ("-o", "pipefail", "-c", "npm test")

    async def test_the_shell_tool_uses_it(self) -> None:
        sandbox = _sandbox()
        tool = ShellCommandTool(sandbox=sandbox)

        with run_identity_scope(
            execution_id="exec-1", task_id="task-1", project_id=_PROJECT
        ):
            await tool.execute(arguments={"command": "npm test | tail -5"})

        assert _recorded_args(sandbox)[: len(SHELL_ARGS_PREFIX)] == SHELL_ARGS_PREFIX

    async def test_the_code_runner_uses_it_for_a_shell_snippet(self) -> None:
        # A bash snippet IS a command line, and its exit status is read as
        # test evidence by the same classifier, so it needs the same shell.
        sandbox = _sandbox()
        tool = CodeRunnerTool(sandbox=sandbox)

        with run_identity_scope(
            execution_id="exec-1", task_id="task-1", project_id=_PROJECT
        ):
            await tool.execute(arguments={"code": "npm test", "language": "bash"})

        assert _recorded_args(sandbox)[: len(SHELL_ARGS_PREFIX)] == SHELL_ARGS_PREFIX

    async def test_a_python_snippet_keeps_its_own_interpreter(self) -> None:
        sandbox = _sandbox()
        tool = CodeRunnerTool(sandbox=sandbox)

        with run_identity_scope(
            execution_id="exec-1", task_id="task-1", project_id=_PROJECT
        ):
            await tool.execute(arguments={"code": "x = 1", "language": "python"})

        assert _recorded_args(sandbox) == ("-c", "x = 1")

    def test_the_classifier_relies_on_exactly_that_flag(self) -> None:
        """The premise, stated where it would break if the flag went away."""
        assert "pipefail" in SHELL_ARGS_PREFIX
        assert is_test_run("npm test | tail -5")
