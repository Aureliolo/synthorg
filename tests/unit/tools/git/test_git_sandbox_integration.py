"""Tests for git tools with sandbox integration."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from synthorg.tools.git_tools import (
    GitBranchTool,
    GitCloneTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitStatusTool,
)
from synthorg.tools.sandbox.errors import SandboxError
from synthorg.tools.sandbox.result import SandboxResult
from synthorg.tools.sandbox.subprocess_sandbox import SubprocessSandbox

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestGitToolsWithSandbox:
    """Git tools work when a sandbox is injected."""

    async def test_status_with_sandbox(self, git_repo: Path) -> None:
        sandbox = SubprocessSandbox(workspace=git_repo)
        tool = GitStatusTool(workspace=git_repo, sandbox=sandbox)
        result = await tool.execute(arguments={})
        assert not result.is_error

    async def test_log_with_sandbox(self, git_repo: Path) -> None:
        sandbox = SubprocessSandbox(workspace=git_repo)
        tool = GitLogTool(workspace=git_repo, sandbox=sandbox)
        result = await tool.execute(arguments={"max_count": 1})
        assert not result.is_error
        assert "initial" in result.content.lower()

    async def test_diff_with_sandbox(self, git_repo: Path) -> None:
        (git_repo / "new.txt").write_text("new content\n")
        sandbox = SubprocessSandbox(workspace=git_repo)
        tool = GitDiffTool(workspace=git_repo, sandbox=sandbox)
        result = await tool.execute(arguments={})
        assert not result.is_error

    async def test_branch_list_with_sandbox(self, git_repo: Path) -> None:
        sandbox = SubprocessSandbox(workspace=git_repo)
        tool = GitBranchTool(workspace=git_repo, sandbox=sandbox)
        result = await tool.execute(arguments={"action": "list"})
        assert not result.is_error

    async def test_commit_with_sandbox(self, git_repo: Path) -> None:
        (git_repo / "staged.txt").write_text("staged\n")
        sandbox = SubprocessSandbox(workspace=git_repo)
        tool = GitCommitTool(workspace=git_repo, sandbox=sandbox)
        result = await tool.execute(
            arguments={
                "message": "sandbox commit",
                "paths": ["staged.txt"],
            },
        )
        assert not result.is_error

    async def test_clone_with_sandbox(self, git_repo: Path) -> None:
        sandbox = SubprocessSandbox(workspace=git_repo)
        tool = GitCloneTool(workspace=git_repo, sandbox=sandbox)
        result = await tool.execute(
            arguments={
                "url": git_repo.as_uri(),
                "directory": "cloned",
            },
        )
        # file:// URLs are intentionally rejected
        assert result.is_error
        assert "Invalid clone URL" in result.content


class TestGitToolsWithoutSandbox:
    """Git tools work without sandbox (backward compat)."""

    async def test_status_without_sandbox(self, git_repo: Path) -> None:
        tool = GitStatusTool(workspace=git_repo)
        result = await tool.execute(arguments={})
        assert not result.is_error

    async def test_log_without_sandbox(self, git_repo: Path) -> None:
        tool = GitLogTool(workspace=git_repo)
        result = await tool.execute(arguments={"max_count": 1})
        assert not result.is_error
        assert "initial" in result.content.lower()


class TestSandboxTimeoutSurfaces:
    """Sandbox timeout surfaces as ToolExecutionResult(is_error=True)."""

    async def test_sandbox_timeout_returns_error(
        self,
        git_repo: Path,
    ) -> None:
        sandbox = SubprocessSandbox(workspace=git_repo)
        sandbox.execute = AsyncMock(  # type: ignore[method-assign]
            return_value=SandboxResult(
                stdout="",
                stderr="Process timed out after 1s",
                returncode=-1,
                timed_out=True,
            ),
        )
        tool = GitStatusTool(workspace=git_repo, sandbox=sandbox)
        result = await tool.execute(arguments={})
        assert result.is_error
        assert "timed out" in result.content.lower()


class TestSandboxErrorSurfaces:
    """SandboxError surfaces as ToolExecutionResult(is_error=True)."""

    async def test_sandbox_error_returns_error(
        self,
        git_repo: Path,
    ) -> None:
        sandbox = SubprocessSandbox(workspace=git_repo)
        sandbox.execute = AsyncMock(  # type: ignore[method-assign]
            side_effect=SandboxError("workspace violation"),
        )
        tool = GitStatusTool(workspace=git_repo, sandbox=sandbox)
        result = await tool.execute(arguments={})
        assert result.is_error
        assert "workspace violation" in result.content


class TestUnexpectedSandboxException:
    """Non-SandboxError exceptions propagate (not swallowed)."""

    async def test_unexpected_exception_propagates(
        self,
        git_repo: Path,
    ) -> None:
        sandbox = SubprocessSandbox(workspace=git_repo)
        sandbox.execute = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("unexpected bug"),
        )
        tool = GitStatusTool(workspace=git_repo, sandbox=sandbox)
        with pytest.raises(RuntimeError, match="unexpected bug"):
            await tool.execute(arguments={})


class TestSandboxResultConversion:
    """_sandbox_result_to_execution_result handles edge cases."""

    async def test_nonzero_returncode_prefers_stderr(
        self,
        git_repo: Path,
    ) -> None:
        sandbox = SubprocessSandbox(workspace=git_repo)
        sandbox.execute = AsyncMock(  # type: ignore[method-assign]
            return_value=SandboxResult(
                stdout="stdout detail",
                stderr="stderr detail",
                returncode=1,
            ),
        )
        tool = GitStatusTool(workspace=git_repo, sandbox=sandbox)
        result = await tool.execute(arguments={})
        assert result.is_error
        assert "stderr detail" in result.content

    async def test_nonzero_returncode_falls_back_to_stdout(
        self,
        git_repo: Path,
    ) -> None:
        sandbox = SubprocessSandbox(workspace=git_repo)
        sandbox.execute = AsyncMock(  # type: ignore[method-assign]
            return_value=SandboxResult(
                stdout="stdout detail",
                stderr="",
                returncode=1,
            ),
        )
        tool = GitStatusTool(workspace=git_repo, sandbox=sandbox)
        result = await tool.execute(arguments={})
        assert result.is_error
        assert "stdout detail" in result.content

    async def test_nonzero_returncode_unknown_error_fallback(
        self,
        git_repo: Path,
    ) -> None:
        sandbox = SubprocessSandbox(workspace=git_repo)
        sandbox.execute = AsyncMock(  # type: ignore[method-assign]
            return_value=SandboxResult(
                stdout="",
                stderr="",
                returncode=1,
            ),
        )
        tool = GitStatusTool(workspace=git_repo, sandbox=sandbox)
        result = await tool.execute(arguments={})
        assert result.is_error
        assert "Unknown git error" in result.content


class TestGitHardeningWithSandbox:
    """Git hardening env vars are passed via env_overrides."""

    async def test_env_overrides_contain_hardening_vars(
        self,
        git_repo: Path,
    ) -> None:
        sandbox = SubprocessSandbox(workspace=git_repo)
        original_execute = sandbox.execute
        captured_overrides: dict[str, str] = {}

        async def capture_execute(**kwargs: object) -> SandboxResult:
            overrides = kwargs.get("env_overrides")
            if isinstance(overrides, dict):
                captured_overrides.update(overrides)
            return await original_execute(**kwargs)  # type: ignore[arg-type]

        sandbox.execute = capture_execute  # type: ignore[method-assign]
        tool = GitStatusTool(workspace=git_repo, sandbox=sandbox)
        await tool.execute(arguments={})

        assert captured_overrides.get("GIT_TERMINAL_PROMPT") == "0"
        assert captured_overrides.get("GIT_CONFIG_NOSYSTEM") == "1"
        assert captured_overrides.get("GIT_PROTOCOL_FROM_USER") == "0"
