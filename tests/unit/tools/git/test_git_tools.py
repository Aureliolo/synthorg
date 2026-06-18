"""Tests for built-in git tools."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools._git_subprocess import _sanitize_command

# GitCloneTool is still in _ALL_GIT_TOOL_CLASSES for property tests.
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.git_tools import (
    GitBranchTool,
    GitCloneTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitStatusTool,
)

from .conftest import _run_git

_ALL_GIT_TOOL_CLASSES = [
    GitStatusTool,
    GitLogTool,
    GitDiffTool,
    GitBranchTool,
    GitCommitTool,
    GitCloneTool,
]

# ── Workspace validation (shared across tools) ───────────────────


@pytest.mark.unit
class TestWorkspaceValidation:
    """Path traversal and boundary enforcement."""

    async def test_path_traversal_blocked(self, status_tool: GitStatusTool) -> None:
        tool = GitLogTool(workspace=status_tool.workspace)
        result = await tool.execute(
            arguments={"paths": ["../../etc/passwd"]},
        )
        assert result.is_error

    async def test_absolute_path_outside_workspace(self, git_repo: Path) -> None:
        tool = GitDiffTool(workspace=git_repo)
        outside = str(git_repo.parent / "outside")
        result = await tool.execute(
            arguments={"paths": [outside]},
        )
        assert result.is_error

    async def test_symlink_escape_blocked(self, git_repo: Path) -> None:
        outside = git_repo.parent / "outside_dir"
        outside.mkdir(exist_ok=True)
        (outside / "secret.txt").write_text("secret")
        link = git_repo / "escape_link"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("Cannot create symlinks")
        tool = GitDiffTool(workspace=git_repo)
        result = await tool.execute(
            arguments={"paths": ["escape_link/secret.txt"]},
        )
        assert result.is_error

    async def test_valid_relative_path_accepted(self, git_repo: Path) -> None:
        tool = GitDiffTool(workspace=git_repo)
        result = await tool.execute(
            arguments={"paths": ["README.md"]},
        )
        assert not result.is_error

    def test_workspace_must_be_absolute(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="absolute path"):
            GitStatusTool(workspace=Path("relative/path"))


# ── Tool properties ──────────────────────────────────────────────


@pytest.mark.unit
class TestToolProperties:
    """Name, description, category, and schema for all git tools."""

    @pytest.mark.parametrize(
        ("tool_cls", "expected_name"),
        [
            (GitStatusTool, "git_status"),
            (GitLogTool, "git_log"),
            (GitDiffTool, "git_diff"),
            (GitBranchTool, "git_branch"),
            (GitCommitTool, "git_commit"),
            (GitCloneTool, "git_clone"),
        ],
    )
    def test_name(
        self,
        tool_cls: type,
        expected_name: str,
        tmp_path: Path,
    ) -> None:
        tool = tool_cls(workspace=tmp_path)
        assert tool.name == expected_name

    @pytest.mark.parametrize("tool_cls", _ALL_GIT_TOOL_CLASSES)
    def test_category_is_version_control(self, tool_cls: type, tmp_path: Path) -> None:
        tool = tool_cls(workspace=tmp_path)
        assert tool.category == ToolCategory.VERSION_CONTROL

    @pytest.mark.parametrize("tool_cls", _ALL_GIT_TOOL_CLASSES)
    def test_has_schema(self, tool_cls: type, tmp_path: Path) -> None:
        tool = tool_cls(workspace=tmp_path)
        schema = tool.parameters_schema
        assert schema is not None
        assert schema["type"] == "object"

    @pytest.mark.parametrize("tool_cls", _ALL_GIT_TOOL_CLASSES)
    def test_description_not_empty(self, tool_cls: type, tmp_path: Path) -> None:
        tool = tool_cls(workspace=tmp_path)
        assert tool.description


# ── GitStatusTool ─────────────────────────────────────────────────


@pytest.mark.unit
class TestGitStatusTool:
    """Tests for git_status."""

    async def test_clean_repo(self, status_tool: GitStatusTool) -> None:
        result = await status_tool.execute(arguments={})
        assert not result.is_error

    async def test_short_format(self, git_repo_with_changes: Path) -> None:
        tool = GitStatusTool(workspace=git_repo_with_changes)
        result = await tool.execute(
            arguments={"short": True},
        )
        assert not result.is_error
        assert result.content

    async def test_porcelain_format(self, git_repo_with_changes: Path) -> None:
        tool = GitStatusTool(workspace=git_repo_with_changes)
        result = await tool.execute(
            arguments={"porcelain": True},
        )
        assert not result.is_error
        assert "README.md" in result.content or "new_file" in result.content

    async def test_not_a_git_repo(self, workspace: Path) -> None:
        tool = GitStatusTool(workspace=workspace)
        result = await tool.execute(arguments={})
        assert result.is_error


# ── GitLogTool ────────────────────────────────────────────────────


@pytest.mark.unit
class TestGitLogTool:
    """Tests for git_log."""

    async def test_shows_initial_commit(self, log_tool: GitLogTool) -> None:
        result = await log_tool.execute(arguments={})
        assert not result.is_error
        assert "initial commit" in result.content.lower()

    async def test_oneline_format(self, log_tool: GitLogTool) -> None:
        result = await log_tool.execute(
            arguments={"oneline": True},
        )
        assert not result.is_error
        lines = result.content.strip().split("\n")
        assert len(lines) >= 1

    async def test_max_count_respected(self, log_tool: GitLogTool) -> None:
        result = await log_tool.execute(
            arguments={"max_count": 1, "oneline": True},
        )
        assert not result.is_error
        lines = result.content.strip().split("\n")
        assert len(lines) == 1

    async def test_max_count_clamped_to_instance_limit(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A per-call ``max_count`` within the model range (<=100) but
        # above the configured instance limit is clamped down.
        tool = GitLogTool(workspace=git_repo, max_count_limit=2)
        captured: list[list[str]] = []

        async def _capture(args: list[str]) -> ToolExecutionResult:
            captured.append(args)
            return ToolExecutionResult(content="x")

        monkeypatch.setattr(tool, "_run_git", _capture)
        await tool.execute(arguments={"max_count": 50})
        assert captured[0][1] == "--max-count=2"

    async def test_max_count_above_model_cap_rejected(
        self, log_tool: GitLogTool
    ) -> None:
        # The args model's ``le=100`` rejects an out-of-range max_count
        # at the typed boundary.
        with pytest.raises(ValidationError, match="max_count"):
            await log_tool.execute(
                arguments={"max_count": 200},
            )

    async def test_empty_repo_no_commits(self, empty_git_repo: Path) -> None:
        tool = GitLogTool(workspace=empty_git_repo)
        result = await tool.execute(arguments={})
        assert result.is_error

    async def test_author_filter(self, log_tool: GitLogTool) -> None:
        result = await log_tool.execute(
            arguments={"author": "NoSuchAuthor"},
        )
        assert not result.is_error
        assert "No commits found" in result.content

    async def test_path_filter(self, log_tool: GitLogTool) -> None:
        result = await log_tool.execute(
            arguments={"paths": ["README.md"]},
        )
        assert not result.is_error
        assert "initial commit" in result.content.lower()

    async def test_ref_parameter(self, log_tool: GitLogTool) -> None:
        result = await log_tool.execute(
            arguments={"ref": "HEAD"},
        )
        assert not result.is_error

    @pytest.mark.parametrize(
        ("param", "key"),
        [("author", "author"), ("since", "since"), ("until", "until")],
        ids=["author", "since", "until"],
    )
    async def test_flag_injection_in_filter_params(
        self, log_tool: GitLogTool, param: str, key: str
    ) -> None:
        """Filter params starting with '-' must be rejected."""
        result = await log_tool.execute(arguments={key: f"--evil-{param}"})
        assert result.is_error
        assert "must not start with '-'" in result.content


# ── GitDiffTool ───────────────────────────────────────────────────


@pytest.mark.unit
class TestGitDiffTool:
    """Tests for git_diff."""

    async def test_no_changes_returns_message(self, diff_tool: GitDiffTool) -> None:
        result = await diff_tool.execute(arguments={})
        assert not result.is_error
        assert result.content == "No changes"

    async def test_unstaged_changes(self, git_repo_with_changes: Path) -> None:
        tool = GitDiffTool(workspace=git_repo_with_changes)
        result = await tool.execute(arguments={})
        assert not result.is_error
        assert "Modified" in result.content or "README" in result.content

    async def test_staged_changes(self, git_repo_with_changes: Path) -> None:
        _run_git(["add", "README.md"], git_repo_with_changes)
        tool = GitDiffTool(workspace=git_repo_with_changes)
        result = await tool.execute(
            arguments={"staged": True},
        )
        assert not result.is_error
        assert "README" in result.content

    async def test_stat_format(self, git_repo_with_changes: Path) -> None:
        tool = GitDiffTool(workspace=git_repo_with_changes)
        result = await tool.execute(arguments={"stat": True})
        assert not result.is_error

    async def test_ref_comparison(self, diff_tool: GitDiffTool) -> None:
        result = await diff_tool.execute(
            arguments={"ref1": "HEAD", "ref2": "HEAD"},
        )
        assert not result.is_error

    async def test_path_filter(self, git_repo_with_changes: Path) -> None:
        tool = GitDiffTool(workspace=git_repo_with_changes)
        result = await tool.execute(
            arguments={"paths": ["README.md"]},
        )
        assert not result.is_error

    async def test_ref2_without_ref1_returns_error(
        self,
        diff_tool: GitDiffTool,
    ) -> None:
        # The args-model validator rejects ``ref2`` without ``ref1`` at
        # the typed boundary.
        with pytest.raises(ValidationError, match="ref2 requires ref1"):
            await diff_tool.execute(
                arguments={"ref2": "HEAD"},
            )


# ── GitBranchTool ─────────────────────────────────────────────────


@pytest.mark.unit
class TestGitBranchTool:
    """Tests for git_branch."""

    async def test_list_branches(self, branch_tool: GitBranchTool) -> None:
        result = await branch_tool.execute(
            arguments={"action": "list"},
        )
        assert not result.is_error

    async def test_create_branch(self, branch_tool: GitBranchTool) -> None:
        result = await branch_tool.execute(
            arguments={
                "action": "create",
                "name": "feature/test",
            },
        )
        assert not result.is_error

    async def test_create_and_switch(self, branch_tool: GitBranchTool) -> None:
        await branch_tool.execute(
            arguments={
                "action": "create",
                "name": "feature/switch-test",
            },
        )
        result = await branch_tool.execute(
            arguments={
                "action": "switch",
                "name": "feature/switch-test",
            },
        )
        assert not result.is_error

    async def test_delete_branch(self, branch_tool: GitBranchTool) -> None:
        await branch_tool.execute(
            arguments={
                "action": "create",
                "name": "to-delete",
            },
        )
        result = await branch_tool.execute(
            arguments={
                "action": "delete",
                "name": "to-delete",
            },
        )
        assert not result.is_error

    async def test_force_delete(self, branch_tool: GitBranchTool) -> None:
        await branch_tool.execute(
            arguments={
                "action": "create",
                "name": "force-del",
            },
        )
        result = await branch_tool.execute(
            arguments={
                "action": "delete",
                "name": "force-del",
                "force": True,
            },
        )
        assert not result.is_error

    async def test_create_with_start_point(self, branch_tool: GitBranchTool) -> None:
        result = await branch_tool.execute(
            arguments={
                "action": "create",
                "name": "from-head",
                "start_point": "HEAD",
            },
        )
        assert not result.is_error

    async def test_name_required_for_create(self, branch_tool: GitBranchTool) -> None:
        # The args-model validator requires a branch ``name`` for the
        # create/switch/delete actions at the typed boundary.
        with pytest.raises(ValidationError, match="name"):
            await branch_tool.execute(
                arguments={"action": "create"},
            )

    async def test_name_required_for_switch(self, branch_tool: GitBranchTool) -> None:
        with pytest.raises(ValidationError, match="name"):
            await branch_tool.execute(
                arguments={"action": "switch"},
            )

    async def test_name_required_for_delete(self, branch_tool: GitBranchTool) -> None:
        with pytest.raises(ValidationError, match="name"):
            await branch_tool.execute(
                arguments={"action": "delete"},
            )

    async def test_switch_nonexistent_branch(self, branch_tool: GitBranchTool) -> None:
        result = await branch_tool.execute(
            arguments={
                "action": "switch",
                "name": "no-such-branch",
            },
        )
        assert result.is_error

    async def test_unknown_branch_action_returns_error(
        self,
        branch_tool: GitBranchTool,
    ) -> None:
        # The GitBranchAction literal rejects an out-of-set action at the
        # typed boundary.
        with pytest.raises(ValidationError, match="action"):
            await branch_tool.execute(
                arguments={"action": "unknown", "name": "x"},
            )


# ── GitCommitTool ─────────────────────────────────────────────────


@pytest.mark.unit
class TestGitCommitTool:
    """Tests for git_commit."""

    async def test_commit_with_paths(self, git_repo_with_changes: Path) -> None:
        tool = GitCommitTool(workspace=git_repo_with_changes)
        result = await tool.execute(
            arguments={
                "message": "add new file",
                "paths": ["new_file.txt"],
            },
        )
        assert not result.is_error

    async def test_commit_all(self, git_repo_with_changes: Path) -> None:
        tool = GitCommitTool(workspace=git_repo_with_changes)
        result = await tool.execute(
            arguments={"message": "commit all", "all": True},
        )
        assert not result.is_error

    async def test_nothing_to_commit(self, commit_tool: GitCommitTool) -> None:
        result = await commit_tool.execute(
            arguments={"message": "empty"},
        )
        assert result.is_error

    async def test_path_traversal_in_commit(self, commit_tool: GitCommitTool) -> None:
        result = await commit_tool.execute(
            arguments={
                "message": "sneaky",
                "paths": ["../../etc/passwd"],
            },
        )
        assert result.is_error


# ── Security: flag injection prevention ───────────────────────────


@pytest.mark.unit
class TestFlagInjectionPrevention:
    """Refs and branch names starting with ``-`` must be rejected."""

    async def test_log_ref_flag_blocked(self, log_tool: GitLogTool) -> None:
        result = await log_tool.execute(
            arguments={"ref": "--exec=malicious"},
        )
        assert result.is_error
        assert "must not start with '-'" in result.content

    async def test_diff_ref1_flag_blocked(self, diff_tool: GitDiffTool) -> None:
        result = await diff_tool.execute(
            arguments={"ref1": "--upload-pack=evil"},
        )
        assert result.is_error

    async def test_diff_ref2_flag_blocked(self, diff_tool: GitDiffTool) -> None:
        result = await diff_tool.execute(
            arguments={"ref1": "HEAD", "ref2": "-c core.sshCommand=evil"},
        )
        assert result.is_error

    async def test_branch_name_flag_blocked(self, branch_tool: GitBranchTool) -> None:
        result = await branch_tool.execute(
            arguments={"action": "create", "name": "--set-upstream-to=evil"},
        )
        assert result.is_error

    async def test_branch_start_point_flag_blocked(
        self, branch_tool: GitBranchTool
    ) -> None:
        result = await branch_tool.execute(
            arguments={
                "action": "create",
                "name": "ok-branch",
                "start_point": "--exec=evil",
            },
        )
        assert result.is_error

    async def test_switch_name_flag_blocked(self, branch_tool: GitBranchTool) -> None:
        result = await branch_tool.execute(
            arguments={"action": "switch", "name": "-c evil"},
        )
        assert result.is_error

    async def test_clone_branch_flag_blocked(
        self,
        clone_tool: GitCloneTool,
    ) -> None:
        result = await clone_tool.execute(
            arguments={
                "url": "https://example.com/repo.git",
                "branch": "--upload-pack=evil",
            },
        )
        assert result.is_error
        assert "must not start with '-'" in result.content


# ── Edge cases: detached HEAD ─────────────────────────────────────


@pytest.mark.unit
class TestDetachedHead:
    """Tools must work correctly in detached HEAD state."""

    async def test_status_in_detached_head(self, detached_head_repo: Path) -> None:
        tool = GitStatusTool(workspace=detached_head_repo)
        result = await tool.execute(arguments={})
        assert not result.is_error

    async def test_log_in_detached_head(self, detached_head_repo: Path) -> None:
        tool = GitLogTool(workspace=detached_head_repo)
        result = await tool.execute(arguments={})
        assert not result.is_error
        assert "initial commit" in result.content.lower()

    async def test_branch_list_in_detached_head(self, detached_head_repo: Path) -> None:
        tool = GitBranchTool(workspace=detached_head_repo)
        result = await tool.execute(arguments={"action": "list"})
        assert not result.is_error
        assert "detached" in result.content.lower() or "HEAD" in result.content


# ── Edge cases: merge conflicts ───────────────────────────────────


@pytest.mark.unit
class TestMergeConflict:
    """Tools must report merge conflict state clearly."""

    async def test_status_shows_conflict(self, merge_conflict_repo: Path) -> None:
        tool = GitStatusTool(workspace=merge_conflict_repo)
        result = await tool.execute(arguments={})
        assert not result.is_error
        content = result.content.lower()
        has_conflict_marker = (
            "unmerged" in content or "both modified" in content or "readme" in content
        )
        assert has_conflict_marker

    async def test_commit_without_staging_fails_during_conflict(
        self, merge_conflict_repo: Path
    ) -> None:
        """Committing without staging unmerged files must fail."""
        tool = GitCommitTool(workspace=merge_conflict_repo)
        result = await tool.execute(
            arguments={"message": "should fail"},
        )
        assert result.is_error


# ── _run_git error paths ─────────────────────────────────────────


@pytest.mark.unit
class TestRunGitErrorPaths:
    """Unit tests for _run_git timeout and OSError handling."""

    async def test_timeout_kills_process(self, status_tool: GitStatusTool) -> None:
        """Timeout kills the process and returns an error result."""
        calls = 0

        async def slow_communicate() -> tuple[bytes, bytes]:
            nonlocal calls
            calls += 1
            if calls == 1:
                await asyncio.Event().wait()  # blocks until cancelled
            return b"", b""

        mock_proc = MagicMock()
        mock_proc.communicate = slow_communicate
        mock_proc.kill = MagicMock()

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            result = await status_tool._run_git(["status"], deadline=0.01)

        assert result.is_error
        assert "timed out" in result.content
        mock_proc.kill.assert_called_once()

    async def test_timeout_includes_stderr_fragment(
        self,
        status_tool: GitStatusTool,
    ) -> None:
        """Timeout message includes stderr when available."""
        calls = 0

        async def slow_then_stderr() -> tuple[bytes, bytes]:
            nonlocal calls
            calls += 1
            if calls == 1:
                await asyncio.Event().wait()  # blocks until cancelled
            return b"", b"fatal: could not access repository"

        mock_proc = MagicMock()
        mock_proc.communicate = slow_then_stderr
        mock_proc.kill = MagicMock()
        mock_proc._transport = None  # no transport to close

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            result = await status_tool._run_git(["status"], deadline=0.01)

        assert result.is_error
        assert "timed out" in result.content
        assert "fatal: could not access repository" in result.content

    async def test_timeout_sanitizes_stderr_control_chars(
        self,
        status_tool: GitStatusTool,
    ) -> None:
        """Control characters in stderr are stripped."""
        calls = 0

        async def slow_then_dirty_stderr() -> tuple[bytes, bytes]:
            nonlocal calls
            calls += 1
            if calls == 1:
                await asyncio.Event().wait()  # blocks until cancelled
            return b"", b"error\x00with\x07control\x1fchars"

        mock_proc = MagicMock()
        mock_proc.communicate = slow_then_dirty_stderr
        mock_proc.kill = MagicMock()
        mock_proc._transport = None

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            result = await status_tool._run_git(["status"], deadline=0.01)

        assert result.is_error
        assert "\x00" not in result.content
        assert "\x07" not in result.content
        assert "error with control chars" in result.content

    async def test_timeout_sanitizes_stderr_truncation(
        self,
        status_tool: GitStatusTool,
    ) -> None:
        """Stderr fragment is truncated to 500 characters."""
        calls = 0

        async def slow_then_long_stderr() -> tuple[bytes, bytes]:
            nonlocal calls
            calls += 1
            if calls == 1:
                await asyncio.Event().wait()  # blocks until cancelled
            return b"", ("x" * 1000).encode()

        mock_proc = MagicMock()
        mock_proc.communicate = slow_then_long_stderr
        mock_proc.kill = MagicMock()
        mock_proc._transport = None

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            result = await status_tool._run_git(["status"], deadline=0.01)

        assert result.is_error
        # 500 chars from stderr + prefix "Git command timed out after 0.01s: "
        stderr_part = result.content.split(": ", 1)[1]
        assert len(stderr_part) == 500

    async def test_oserror_returns_error(self, status_tool: GitStatusTool) -> None:
        """OSError when git binary not found returns error result."""
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("git not found"),
        ):
            result = await status_tool._run_git(["status"])

        assert result.is_error
        assert "Failed to start git" in result.content


# ── Credential sanitization ───────────────────────────────────────


@pytest.mark.unit
class TestCredentialSanitization:
    """Tests for _sanitize_command credential redaction."""

    def test_credentials_redacted(self) -> None:
        args = ["git", "clone", "https://user:token@github.com/repo.git"]
        result = _sanitize_command(args)
        assert result == ["git", "clone", "https://***@github.com/repo.git"]

    def test_no_credentials_unchanged(self) -> None:
        args = ["git", "clone", "https://github.com/repo.git"]
        result = _sanitize_command(args)
        assert result == args

    def test_scp_like_unchanged(self) -> None:
        args = ["git", "clone", "git@github.com:user/repo.git"]
        result = _sanitize_command(args)
        assert result == args

    def test_non_url_unchanged(self) -> None:
        args = ["git", "status", "--short"]
        result = _sanitize_command(args)
        assert result == args


# ── Error handling edge cases ─────────────────────────────────────


@pytest.mark.unit
class TestErrorHandling:
    """Edge cases and error conditions."""

    async def test_not_a_git_repo(self, workspace: Path) -> None:
        tool = GitStatusTool(workspace=workspace)
        result = await tool.execute(arguments={})
        assert result.is_error

    def test_workspace_property(self, git_repo: Path) -> None:
        tool = GitStatusTool(workspace=git_repo)
        assert tool.workspace == git_repo.resolve()

    async def test_to_definition(self, git_repo: Path) -> None:
        tool = GitStatusTool(workspace=git_repo)
        defn = tool.to_definition()
        assert defn.name == "git_status"
        assert defn.description
