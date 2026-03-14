"""Integration tests for subprocess sandbox with real git."""

import os
from pathlib import Path

import pytest

from synthorg.tools.git_tools import GitStatusTool
from synthorg.tools.sandbox.config import SubprocessSandboxConfig
from synthorg.tools.sandbox.errors import SandboxError
from synthorg.tools.sandbox.subprocess_sandbox import SubprocessSandbox

pytestmark = [pytest.mark.integration, pytest.mark.timeout(30)]


class TestRealGitWithSandbox:
    """Real git repo + SubprocessSandbox + GitStatusTool."""

    async def test_git_status_via_sandbox(self, git_repo: Path) -> None:
        sandbox = SubprocessSandbox(workspace=git_repo)
        tool = GitStatusTool(workspace=git_repo, sandbox=sandbox)
        result = await tool.execute(arguments={})
        assert not result.is_error

    async def test_git_status_porcelain_via_sandbox(
        self,
        git_repo: Path,
    ) -> None:
        (git_repo / "new.txt").write_text("new file")
        sandbox = SubprocessSandbox(workspace=git_repo)
        tool = GitStatusTool(workspace=git_repo, sandbox=sandbox)
        result = await tool.execute(arguments={"porcelain": True})
        assert not result.is_error
        assert "new.txt" in result.content


class TestSandboxWorkspaceEscape:
    """Sandbox blocks workspace escape in real subprocess."""

    async def test_cwd_escape_blocked(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        sandbox = SubprocessSandbox(workspace=workspace)
        with pytest.raises(SandboxError, match="outside workspace"):
            await sandbox.execute(
                command="echo",
                args=("test",),
                cwd=outside,
            )


class TestSandboxTimeout:
    """Sandbox timeout on slow command."""

    async def test_timeout_on_slow_command(self, tmp_path: Path) -> None:
        sandbox = SubprocessSandbox(
            workspace=tmp_path,
            config=SubprocessSandboxConfig(timeout_seconds=1.0),
        )
        if os.name == "nt":
            result = await sandbox.execute(
                command="cmd",
                args=("/c", "ping", "-n", "10", "127.0.0.1"),
                timeout=0.5,
            )
        else:
            result = await sandbox.execute(
                command="sleep",
                args=("10",),
                timeout=0.5,
            )
        assert result.timed_out
        assert not result.success
