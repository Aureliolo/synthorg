"""Integration tests for workspace isolation using real git operations.

These tests create temporary git repositories and exercise the full
PlannerWorktreeStrategy lifecycle with real git commands.
"""

import subprocess
from pathlib import Path

import pytest

from synthorg.engine.errors import WorkspaceLimitError
from synthorg.engine.workspace.config import (
    PlannerWorktreesConfig,
)
from synthorg.engine.workspace.git_worktree import (
    PlannerWorktreeStrategy,
)
from synthorg.engine.workspace.models import WorkspaceRequest

pytestmark = [pytest.mark.integration, pytest.mark.timeout(30)]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _init_test_repo(repo_path: Path) -> None:
    """Initialize a git repository with an initial commit.

    Args:
        repo_path: Path where the repo will be created.
    """
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--initial-branch=main"],  # noqa: S607
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],  # noqa: S607
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],  # noqa: S607
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )
    # Create initial commit on main
    readme = repo_path / "README.md"
    readme.write_text("# Test Repo\n")
    subprocess.run(
        ["git", "add", "."],  # noqa: S607
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],  # noqa: S607
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )


def _commit_file(
    repo_path: Path,
    filename: str,
    content: str,
    message: str,
) -> None:
    """Create/update a file and commit it.

    Args:
        repo_path: Path to the repository.
        filename: File to create/modify.
        content: File content.
        message: Commit message.
    """
    filepath = repo_path / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content)
    subprocess.run(  # noqa: S603
        ["git", "add", filename],  # noqa: S607
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )
    subprocess.run(  # noqa: S603
        ["git", "commit", "-m", message],  # noqa: S607
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )


def _make_strategy(
    repo_path: Path,
    *,
    max_worktrees: int = 8,
) -> PlannerWorktreeStrategy:
    """Create a strategy pointing at the test repo.

    Args:
        repo_path: Path to the test repository.
        max_worktrees: Maximum concurrent worktrees.

    Returns:
        Configured PlannerWorktreeStrategy.
    """
    worktree_dir = repo_path.parent / ".worktrees"
    return PlannerWorktreeStrategy(
        config=PlannerWorktreesConfig(
            max_concurrent_worktrees=max_worktrees,
            worktree_base_dir=str(worktree_dir),
        ),
        repo_root=repo_path,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDifferentFilesNoConflict:
    """Two agents edit different files -> merge succeeds."""

    async def test_merge_different_files(
        self,
        tmp_path: Path,
    ) -> None:
        """Workspaces editing different files merge cleanly."""
        repo = tmp_path / "repo"
        _init_test_repo(repo)

        strategy = _make_strategy(repo)

        # Setup two workspaces
        ws1 = await strategy.setup_workspace(
            request=WorkspaceRequest(
                task_id="task-a",
                agent_id="agent-1",
            ),
        )
        ws2 = await strategy.setup_workspace(
            request=WorkspaceRequest(
                task_id="task-b",
                agent_id="agent-2",
            ),
        )

        # Agent 1 edits file_a.py in its worktree
        _commit_file(
            Path(ws1.worktree_path),
            "file_a.py",
            "print('hello from agent 1')\n",
            "Add file_a",
        )

        # Agent 2 edits file_b.py in its worktree
        _commit_file(
            Path(ws2.worktree_path),
            "file_b.py",
            "print('hello from agent 2')\n",
            "Add file_b",
        )

        # Merge both back
        result1 = await strategy.merge_workspace(workspace=ws1)
        assert result1.success is True
        assert result1.merged_commit_sha is not None

        result2 = await strategy.merge_workspace(workspace=ws2)
        assert result2.success is True
        assert result2.merged_commit_sha is not None

        # Cleanup
        await strategy.teardown_workspace(workspace=ws1)
        await strategy.teardown_workspace(workspace=ws2)

        active = await strategy.list_active_workspaces()
        assert len(active) == 0


class TestSameFileConflict:
    """Two agents edit same file -> conflict detected."""

    async def test_merge_same_file_conflict(
        self,
        tmp_path: Path,
    ) -> None:
        """Workspaces editing the same file produce a conflict."""
        repo = tmp_path / "repo"
        _init_test_repo(repo)

        # Create a shared file in main
        _commit_file(
            repo,
            "shared.py",
            "# shared module\nvalue = 1\n",
            "Add shared.py",
        )

        strategy = _make_strategy(repo)

        ws1 = await strategy.setup_workspace(
            request=WorkspaceRequest(
                task_id="task-c",
                agent_id="agent-1",
            ),
        )
        ws2 = await strategy.setup_workspace(
            request=WorkspaceRequest(
                task_id="task-d",
                agent_id="agent-2",
            ),
        )

        # Both edit the same line in shared.py
        _commit_file(
            Path(ws1.worktree_path),
            "shared.py",
            "# shared module\nvalue = 100\n",
            "Agent 1 changes value",
        )
        _commit_file(
            Path(ws2.worktree_path),
            "shared.py",
            "# shared module\nvalue = 200\n",
            "Agent 2 changes value",
        )

        # First merge succeeds
        result1 = await strategy.merge_workspace(workspace=ws1)
        assert result1.success is True

        # Second merge conflicts
        result2 = await strategy.merge_workspace(workspace=ws2)
        assert result2.success is False
        assert len(result2.conflicts) > 0
        conflict_files = {c.file_path for c in result2.conflicts}
        assert "shared.py" in conflict_files

        # Cleanup
        await strategy.teardown_workspace(workspace=ws1)
        await strategy.teardown_workspace(workspace=ws2)


class TestWorktreeCleanup:
    """Worktree cleanup removes directory and branch."""

    async def test_teardown_removes_directory_and_branch(
        self,
        tmp_path: Path,
    ) -> None:
        """Teardown removes the worktree directory and branch."""
        repo = tmp_path / "repo"
        _init_test_repo(repo)

        strategy = _make_strategy(repo)

        ws = await strategy.setup_workspace(
            request=WorkspaceRequest(
                task_id="task-e",
                agent_id="agent-1",
            ),
        )

        worktree_dir = Path(ws.worktree_path)
        assert worktree_dir.exists()  # noqa: ASYNC240

        await strategy.teardown_workspace(workspace=ws)

        # Worktree directory should be gone
        assert not worktree_dir.exists()  # noqa: ASYNC240

        # Branch should be gone
        result = subprocess.run(  # noqa: ASYNC221, S603
            ["git", "branch", "--list", ws.branch_name],  # noqa: S607
            cwd=str(repo),
            capture_output=True,
            check=False,
            text=True,
        )
        assert ws.branch_name not in result.stdout

        # No active workspaces
        active = await strategy.list_active_workspaces()
        assert len(active) == 0


class TestWorktreeLimitEnforcement:
    """Worktree limit is enforced."""

    async def test_limit_raises_workspace_limit_error(
        self,
        tmp_path: Path,
    ) -> None:
        """Exceeding max_concurrent_worktrees raises error."""
        repo = tmp_path / "repo"
        _init_test_repo(repo)

        strategy = _make_strategy(repo, max_worktrees=1)

        await strategy.setup_workspace(
            request=WorkspaceRequest(
                task_id="task-f",
                agent_id="agent-1",
            ),
        )

        with pytest.raises(WorkspaceLimitError):
            await strategy.setup_workspace(
                request=WorkspaceRequest(
                    task_id="task-g",
                    agent_id="agent-2",
                ),
            )
