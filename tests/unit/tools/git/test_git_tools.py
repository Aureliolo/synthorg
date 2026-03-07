"""Tests for built-in git tools."""

from pathlib import Path  # noqa: TC003 — used at runtime

import pytest

from ai_company.core.enums import ToolCategory
from ai_company.tools.git_tools import (
    GitBranchTool,
    GitCloneTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitStatusTool,
)

from .conftest import _run_git

pytestmark = pytest.mark.timeout(30)


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
        outside.mkdir()
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

    @pytest.mark.parametrize(
        "tool_cls",
        [
            GitStatusTool,
            GitLogTool,
            GitDiffTool,
            GitBranchTool,
            GitCommitTool,
            GitCloneTool,
        ],
    )
    def test_category_is_version_control(self, tool_cls: type, tmp_path: Path) -> None:
        tool = tool_cls(workspace=tmp_path)
        assert tool.category == ToolCategory.VERSION_CONTROL

    @pytest.mark.parametrize(
        "tool_cls",
        [
            GitStatusTool,
            GitLogTool,
            GitDiffTool,
            GitBranchTool,
            GitCommitTool,
            GitCloneTool,
        ],
    )
    def test_has_schema(self, tool_cls: type, tmp_path: Path) -> None:
        tool = tool_cls(workspace=tmp_path)
        schema = tool.parameters_schema
        assert schema is not None
        assert schema["type"] == "object"

    @pytest.mark.parametrize(
        "tool_cls",
        [
            GitStatusTool,
            GitLogTool,
            GitDiffTool,
            GitBranchTool,
            GitCommitTool,
            GitCloneTool,
        ],
    )
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

    async def test_max_count_clamped(self, log_tool: GitLogTool) -> None:
        result = await log_tool.execute(
            arguments={"max_count": 200},
        )
        assert not result.is_error

    async def test_empty_repo_no_commits(self, empty_git_repo: Path) -> None:
        tool = GitLogTool(workspace=empty_git_repo)
        result = await tool.execute(arguments={})
        assert result.is_error or "no commits" in result.content.lower()

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
        result = await branch_tool.execute(
            arguments={"action": "create"},
        )
        assert result.is_error
        assert "required" in result.content.lower()

    async def test_name_required_for_switch(self, branch_tool: GitBranchTool) -> None:
        result = await branch_tool.execute(
            arguments={"action": "switch"},
        )
        assert result.is_error

    async def test_name_required_for_delete(self, branch_tool: GitBranchTool) -> None:
        result = await branch_tool.execute(
            arguments={"action": "delete"},
        )
        assert result.is_error

    async def test_switch_nonexistent_branch(self, branch_tool: GitBranchTool) -> None:
        result = await branch_tool.execute(
            arguments={
                "action": "switch",
                "name": "no-such-branch",
            },
        )
        assert result.is_error


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


# ── GitCloneTool ──────────────────────────────────────────────────


@pytest.mark.unit
class TestGitCloneTool:
    """Tests for git_clone."""

    async def test_clone_local_repo(self, git_repo: Path, workspace: Path) -> None:
        tool = GitCloneTool(workspace=workspace)
        result = await tool.execute(
            arguments={
                "url": str(git_repo),
                "directory": "cloned",
            },
        )
        assert not result.is_error
        assert (workspace / "cloned" / "README.md").exists()

    async def test_clone_with_depth(self, git_repo: Path, workspace: Path) -> None:
        tool = GitCloneTool(workspace=workspace)
        result = await tool.execute(
            arguments={
                "url": str(git_repo),
                "directory": "shallow",
                "depth": 1,
            },
        )
        assert not result.is_error

    async def test_clone_directory_outside_workspace(
        self, git_repo: Path, workspace: Path
    ) -> None:
        tool = GitCloneTool(workspace=workspace)
        result = await tool.execute(
            arguments={
                "url": str(git_repo),
                "directory": "../../outside",
            },
        )
        assert result.is_error

    async def test_clone_invalid_url(self, clone_tool: GitCloneTool) -> None:
        result = await clone_tool.execute(
            arguments={"url": "not-a-real-url-at-all"},
        )
        assert result.is_error

    async def test_clone_with_branch(self, git_repo: Path, workspace: Path) -> None:
        _run_git(["branch", "test-branch"], git_repo)
        tool = GitCloneTool(workspace=workspace)
        result = await tool.execute(
            arguments={
                "url": str(git_repo),
                "directory": "branch-clone",
                "branch": "test-branch",
            },
        )
        assert not result.is_error


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
