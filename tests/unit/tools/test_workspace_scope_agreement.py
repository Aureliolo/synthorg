"""What an agent writes is what its commands can run.

File tools resolve their root per call from the bound execution identity, so
every write lands in ``<base>/projects/<project_id>``. A sandbox handed no
project mounts ``<base>`` instead, one directory above everything just
written: ``write_file`` succeeds, the file exists on disk, and ``python -c
'import textkit'`` in the very next turn raises ModuleNotFoundError. Nothing
fails loudly, so the loop keeps going and reports itself complete having
delivered nothing the checks can find.

That root is also every other project's files, so the unscoped case is refused
rather than run one directory up.
"""

from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.core.execution_identity import run_identity_scope
from synthorg.engine.workspace.paths import project_workspace_dir
from synthorg.tools.code_runner import CodeRunnerTool
from synthorg.tools.file_system.write_file import WriteFileTool
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
    sandbox = mock_of[SandboxBackend](
        execute=AsyncMock(
            return_value=SandboxResult(stdout="", stderr="", returncode=0)
        )
    )
    return cast("SandboxBackend", sandbox)


def _project_id_of_last_execute(sandbox: SandboxBackend) -> str | None:
    """Return the project id the tool passed to the sandbox.

    Args:
        sandbox: The sandbox double the tool ran against.

    Returns:
        The ``project_id`` keyword of the recorded call.
    """
    recorded = cast("AsyncMock", sandbox.execute).await_args
    assert recorded is not None
    project_id = recorded.kwargs.get("project_id")
    return str(project_id) if project_id is not None else None


class TestShellScope:
    async def test_a_command_runs_in_the_running_project(self) -> None:
        sandbox = _sandbox()
        tool = ShellCommandTool(sandbox=sandbox)

        with run_identity_scope(
            execution_id="exec-1", task_id="task-1", project_id=_PROJECT
        ):
            await tool.execute(arguments={"command": "true"})

        assert _project_id_of_last_execute(sandbox) == _PROJECT

    async def test_no_project_refuses_rather_than_widening_to_the_root(self) -> None:
        # A tool exercised outside a run, or a run with no project, has no
        # subtree to select. Passing no project selects the whole workspace,
        # which is every other project's files, so the command is refused
        # instead: the agent is told, and the sandbox is never reached.
        sandbox = _sandbox()
        tool = ShellCommandTool(sandbox=sandbox)

        result = await tool.execute(arguments={"command": "true"})

        assert result.is_error is True
        assert "no project" in result.content
        assert cast("AsyncMock", sandbox.execute).await_args is None


class TestCodeRunnerScope:
    async def test_a_snippet_runs_in_the_running_project(self) -> None:
        # The snippet and the shell write the same test-run evidence, so a
        # scope that held for one and not the other would make which tool the
        # agent happened to pick decide whether its own files are importable.
        sandbox = _sandbox()
        tool = CodeRunnerTool(sandbox=sandbox)

        with run_identity_scope(
            execution_id="exec-1", task_id="task-1", project_id=_PROJECT
        ):
            await tool.execute(arguments={"code": "pass", "language": "python"})

        assert _project_id_of_last_execute(sandbox) == _PROJECT


class TestAgreementWithFileTools:
    async def test_both_halves_name_the_same_tree(self, tmp_path: Path) -> None:
        sandbox = _sandbox()
        shell = ShellCommandTool(sandbox=sandbox)
        writer = WriteFileTool(workspace_root=tmp_path)

        with run_identity_scope(
            execution_id="exec-1", task_id="task-1", project_id=_PROJECT
        ):
            await writer.execute(arguments={"path": "textkit.py", "content": "x = 1\n"})
            await shell.execute(arguments={"command": "python -c 'import textkit'"})

        written = project_workspace_dir(tmp_path, _PROJECT) / "textkit.py"
        assert written.is_file()
        # The sandbox resolves <base>/projects/<project_id> from this id, so
        # the mount the command sees is the directory the file landed in.
        assert _project_id_of_last_execute(sandbox) == _PROJECT
