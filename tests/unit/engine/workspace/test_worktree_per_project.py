"""PlannerWorktreeStrategy per-project repo-root routing.

A worktree for a project must branch from (and later merge/teardown in)
that project's own persistent repo at ``<root>/projects/<project_id>``,
not the strategy's singleton root. These tests pin the routing by
asserting the ``cwd`` every git invocation runs in.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthorg.engine.errors import WorkspaceSetupError
from synthorg.engine.workspace.config import PlannerWorktreesConfig
from synthorg.engine.workspace.git_worktree import PlannerWorktreeStrategy
from synthorg.engine.workspace.models import Workspace, WorkspaceRequest

pytestmark = pytest.mark.unit

_ROOT = Path("/srv/agent-workspace")


def _strategy() -> PlannerWorktreeStrategy:
    return PlannerWorktreeStrategy(
        config=PlannerWorktreesConfig(),
        repo_root=_ROOT,
        cmd_timeout=60.0,
    )


class _GitRecorder:
    """Records the cwd each git invocation runs in; always succeeds."""

    def __init__(self) -> None:
        self.cwds: list[Path] = []

    async def __call__(
        self,
        repo_root: Path,
        *args: str,
        cmd_timeout: float,
        log_event: str,
    ) -> tuple[int, str, str]:
        self.cwds.append(repo_root)
        if args[:1] == ("rev-parse",):
            return 0, "deadbeefcafe", ""
        return 0, "", ""


class TestEffectiveRoot:
    def test_none_is_singleton_root(self) -> None:
        assert _strategy()._effective_root(None) == _ROOT

    def test_project_id_is_project_subtree(self) -> None:
        assert _strategy()._effective_root("p1") == _ROOT / "projects" / "p1"

    @pytest.mark.parametrize("bad", ["../escape", "a/b", "a\\b", "..", ".", "", "   "])
    def test_traversal_rejected(self, bad: str) -> None:
        with pytest.raises(WorkspaceSetupError, match="path-separator"):
            _strategy()._effective_root(bad)

    def test_symlinked_project_escaping_subtree_rejected(self, tmp_path: Path) -> None:
        strategy = PlannerWorktreeStrategy(
            config=PlannerWorktreesConfig(),
            repo_root=tmp_path,
            cmd_timeout=60.0,
        )
        projects = tmp_path / "projects"
        projects.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        try:
            (projects / "evil").symlink_to(outside, target_is_directory=True)
        except OSError, NotImplementedError:
            pytest.skip("symlink creation not permitted on this platform")
        with pytest.raises(WorkspaceSetupError, match="escapes"):
            strategy._effective_root("evil")


class TestSetupRoutesToProjectRoot:
    async def test_setup_runs_git_in_project_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorder = _GitRecorder()
        monkeypatch.setattr(
            "synthorg.engine.workspace.git_worktree.run_git_subprocess",
            recorder,
        )
        strategy = _strategy()
        workspace = await strategy.setup_workspace(
            request=WorkspaceRequest(
                task_id="task-1",
                agent_id="agent-1",
                project_id="proj-a",
            ),
        )

        expected = _ROOT / "projects" / "proj-a"
        assert workspace.project_id == "proj-a"
        # branch + worktree-add both ran in the project repo.
        assert recorder.cwds
        assert all(cwd == expected for cwd in recorder.cwds)
        # The worktree dir is anchored *inside* the project tree so the
        # project-scoped sandbox mount can reach it.
        assert "/projects/proj-a/.worktrees/" in workspace.worktree_path.replace(
            "\\", "/"
        )

    async def test_setup_without_project_uses_singleton_root(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorder = _GitRecorder()
        monkeypatch.setattr(
            "synthorg.engine.workspace.git_worktree.run_git_subprocess",
            recorder,
        )
        strategy = _strategy()
        workspace = await strategy.setup_workspace(
            request=WorkspaceRequest(task_id="task-1", agent_id="agent-1"),
        )

        assert workspace.project_id is None
        assert all(cwd == _ROOT for cwd in recorder.cwds)


class TestMergeRoutesToProjectRoot:
    async def test_merge_runs_git_in_project_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorder = _GitRecorder()
        monkeypatch.setattr(
            "synthorg.engine.workspace.git_worktree.run_git_subprocess",
            recorder,
        )
        strategy = _strategy()
        workspace = Workspace(
            workspace_id="w1",
            task_id="task-1",
            agent_id="agent-1",
            branch_name="workspace/task-1/w1",
            worktree_path="/srv/agent-workspace/projects/proj-a/.worktrees/w1",
            base_branch="main",
            created_at=datetime.now(UTC),
            project_id="proj-a",
        )

        result = await strategy.merge_workspace(workspace=workspace)

        expected = _ROOT / "projects" / "proj-a"
        assert result.success
        assert recorder.cwds
        assert all(cwd == expected for cwd in recorder.cwds)


class TestTeardownRoutesToProjectRoot:
    async def test_teardown_runs_git_in_project_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorder = _GitRecorder()
        monkeypatch.setattr(
            "synthorg.engine.workspace.git_worktree.run_git_subprocess",
            recorder,
        )
        strategy = _strategy()
        workspace = Workspace(
            workspace_id="w1",
            task_id="task-1",
            agent_id="agent-1",
            branch_name="workspace/task-1/w1",
            worktree_path="/srv/agent-workspace/projects/proj-a/.worktrees/w1",
            base_branch="main",
            created_at=datetime.now(UTC),
            project_id="proj-a",
        )
        # Register the workspace so teardown finds it.
        strategy._active_workspaces[workspace.workspace_id] = workspace

        await strategy.teardown_workspace(workspace=workspace)

        expected = _ROOT / "projects" / "proj-a"
        # worktree-remove + branch-delete both ran in the project repo.
        assert recorder.cwds
        assert all(cwd == expected for cwd in recorder.cwds)
