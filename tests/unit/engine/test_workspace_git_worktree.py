"""Tests for PlannerWorktreeStrategy (git worktree backend)."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.engine.errors import (
    WorkspaceCleanupError,
    WorkspaceLimitError,
    WorkspaceMergeError,
    WorkspaceSetupError,
)
from synthorg.engine.workspace.config import (
    PlannerWorktreesConfig,
    SemanticAnalysisConfig,
)
from synthorg.engine.workspace.git_worktree import PlannerWorktreeStrategy
from synthorg.engine.workspace.models import (
    Workspace,
    WorkspaceRequest,
)
from synthorg.engine.workspace.protocol import (
    WorkspaceIsolationStrategy,
)
from synthorg.engine.workspace.semantic_analyzer import SemanticAnalyzer
from synthorg.engine.workspace.semantic_git_ops import (
    _validate_file_path,
    get_base_sources,
    get_changed_files,
    get_merge_base,
    run_semantic_analysis,
)

from .conftest import make_workspace

# Branch names are now workspace/{task_id}/{workspace_id},
# so exact branch name matching requires a pattern prefix check.

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    max_concurrent_worktrees: int = 8,
) -> PlannerWorktreesConfig:
    return PlannerWorktreesConfig(
        max_concurrent_worktrees=max_concurrent_worktrees,
    )


def _make_strategy(
    *,
    config: PlannerWorktreesConfig | None = None,
    repo_root: Path = Path("/fake/repo"),
    semantic_analyzer: object | None = None,
) -> PlannerWorktreeStrategy:
    return PlannerWorktreeStrategy(
        config=config or _make_config(),
        repo_root=repo_root,
        cmd_timeout=60.0,
        semantic_analyzer=semantic_analyzer,  # type: ignore[arg-type]
    )


def _make_request(
    *,
    task_id: str = "task-1",
    agent_id: str = "agent-1",
    base_branch: str = "main",
) -> WorkspaceRequest:
    return WorkspaceRequest(
        task_id=task_id,
        agent_id=agent_id,
        base_branch=base_branch,
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """PlannerWorktreeStrategy satisfies WorkspaceIsolationStrategy."""

    @pytest.mark.unit
    def test_isinstance_check(self) -> None:
        """Strategy passes runtime protocol check."""
        strategy = _make_strategy()
        assert isinstance(strategy, WorkspaceIsolationStrategy)


# ---------------------------------------------------------------------------
# get_strategy_type
# ---------------------------------------------------------------------------


class TestGetStrategyType:
    """Tests for get_strategy_type method."""

    @pytest.mark.unit
    def test_returns_planner_worktrees(self) -> None:
        """Returns the correct strategy type string."""
        strategy = _make_strategy()
        assert strategy.get_strategy_type() == "planner_worktrees"


# ---------------------------------------------------------------------------
# setup_workspace
# ---------------------------------------------------------------------------


class TestSetupWorkspace:
    """Tests for setup_workspace method."""

    @pytest.mark.unit
    async def test_setup_creates_branch_and_worktree(self) -> None:
        """Setup creates git branch and worktree, returns Workspace."""
        strategy = _make_strategy()
        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git, return_value=(0, "", "")
        )

        with patch.object(
            PlannerWorktreeStrategy,
            "_run_git",
            mock_run_git,
        ):
            ws = await strategy.setup_workspace(
                request=_make_request(),
            )

        assert ws.task_id == "task-1"
        assert ws.agent_id == "agent-1"
        assert ws.base_branch == "main"
        assert ws.branch_name.startswith("workspace/task-1/")
        assert ws.workspace_id  # non-empty UUID
        assert ws.worktree_path  # non-empty path
        assert ws.created_at is not None  # datetime

        # Verify git command arguments
        assert mock_run_git.call_count == 2
        first_call = mock_run_git.call_args_list[0]
        assert first_call.args[0] == "branch"
        assert first_call.args[1].startswith("workspace/task-1/")
        assert first_call.args[2] == "main"
        second_call = mock_run_git.call_args_list[1]
        assert second_call.args[0] == "worktree"
        assert second_call.args[1] == "add"

    @pytest.mark.unit
    async def test_setup_at_limit_raises(self) -> None:
        """Setup raises WorkspaceLimitError when at max."""
        strategy = _make_strategy(
            config=_make_config(
                max_concurrent_worktrees=1,
            )
        )
        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git, return_value=(0, "", "")
        )

        with patch.object(
            PlannerWorktreeStrategy,
            "_run_git",
            mock_run_git,
        ):
            await strategy.setup_workspace(
                request=_make_request(task_id="task-1"),
            )

            with pytest.raises(WorkspaceLimitError):
                await strategy.setup_workspace(
                    request=_make_request(task_id="task-2"),
                )

    @pytest.mark.unit
    async def test_setup_branch_failure_raises(self) -> None:
        """Setup raises WorkspaceSetupError on git branch failure."""
        strategy = _make_strategy()
        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git,
            return_value=(1, "", "fatal: branch already exists"),
        )

        with (
            patch.object(
                PlannerWorktreeStrategy,
                "_run_git",
                mock_run_git,
            ),
            pytest.raises(WorkspaceSetupError),
        ):
            await strategy.setup_workspace(
                request=_make_request(),
            )

    @pytest.mark.unit
    async def test_setup_worktree_failure_cleans_branch(self) -> None:
        """Worktree failure cleans up the already-created branch."""
        strategy = _make_strategy()
        # branch succeeds, worktree fails, branch cleanup succeeds
        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git,
            side_effect=[
                (0, "", ""),  # branch
                (1, "", "fatal: worktree path already exists"),  # worktree
                (0, "", ""),  # branch -D cleanup
            ],
        )

        with (
            patch.object(
                PlannerWorktreeStrategy,
                "_run_git",
                mock_run_git,
            ),
            pytest.raises(WorkspaceSetupError),
        ):
            await strategy.setup_workspace(
                request=_make_request(),
            )

        # Verify branch cleanup was attempted
        assert mock_run_git.call_count == 3
        cleanup_call = mock_run_git.call_args_list[2]
        assert cleanup_call.args[0] == "branch"
        assert cleanup_call.args[1] == "-D"
        assert cleanup_call.args[2].startswith("workspace/task-1/")

    @pytest.mark.unit
    async def test_setup_rejects_unsafe_task_id(self) -> None:
        """Setup rejects task_id starting with dash."""
        strategy = _make_strategy()
        with pytest.raises(WorkspaceSetupError, match="Unsafe task_id"):
            await strategy.setup_workspace(
                request=_make_request(task_id="--upload-pack=evil"),
            )

    @pytest.mark.unit
    async def test_setup_rejects_unsafe_base_branch(self) -> None:
        """Setup rejects base_branch with unsafe characters."""
        strategy = _make_strategy()
        with pytest.raises(WorkspaceSetupError, match="Unsafe base_branch"):
            await strategy.setup_workspace(
                request=_make_request(base_branch="--option"),
            )

    @pytest.mark.unit
    async def test_setup_rejects_path_traversal(self) -> None:
        """Task ID with '..' is rejected to prevent namespace escape."""
        strategy = _make_strategy()
        request = WorkspaceRequest(
            task_id="../main",
            agent_id="agent-1",
        )
        with pytest.raises(WorkspaceSetupError, match="Unsafe"):
            await strategy.setup_workspace(request=request)


# ---------------------------------------------------------------------------
# merge_workspace
# ---------------------------------------------------------------------------


class TestMergeWorkspace:
    """Tests for merge_workspace method."""

    @pytest.mark.unit
    async def test_merge_success(self) -> None:
        """Successful merge returns MergeResult(success=True)."""
        strategy = _make_strategy()
        ws = make_workspace()
        strategy._active_workspaces[ws.workspace_id] = ws

        # checkout, pre-merge rev-parse, merge, post-merge rev-parse
        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git,
            side_effect=[
                (0, "", ""),  # checkout base
                (0, "pre123", ""),  # rev-parse HEAD (pre-merge)
                (0, "", ""),  # merge --no-ff
                (0, "abc123", ""),  # rev-parse HEAD (post-merge)
            ],
        )

        with patch.object(
            PlannerWorktreeStrategy,
            "_run_git",
            mock_run_git,
        ):
            result = await strategy.merge_workspace(workspace=ws)

        assert result.success is True
        assert result.conflicts == ()
        assert result.merged_commit_sha == "abc123"
        assert result.duration_seconds >= 0.0

    @pytest.mark.unit
    async def test_merge_with_conflict(self) -> None:
        """Merge conflict returns MergeResult(success=False)."""
        strategy = _make_strategy()
        ws = make_workspace()
        strategy._active_workspaces[ws.workspace_id] = ws

        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git,
            side_effect=[
                (0, "", ""),  # checkout base
                (0, "pre123", ""),  # rev-parse HEAD (pre-merge)
                (1, "", "CONFLICT (content)"),  # merge fails
                (0, "src/main.py\n", ""),  # diff --name-only
                (0, "", ""),  # merge --abort
            ],
        )

        with patch.object(
            PlannerWorktreeStrategy,
            "_run_git",
            mock_run_git,
        ):
            result = await strategy.merge_workspace(workspace=ws)

        assert result.success is False
        assert len(result.conflicts) == 1
        assert result.conflicts[0].file_path == "src/main.py"
        assert result.merged_commit_sha is None

    @pytest.mark.unit
    async def test_merge_checkout_failure_raises(self) -> None:
        """Merge raises WorkspaceMergeError on checkout failure."""
        strategy = _make_strategy()
        ws = make_workspace()
        strategy._active_workspaces[ws.workspace_id] = ws

        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git,
            return_value=(1, "", "error: checkout failed"),
        )

        with (
            patch.object(
                PlannerWorktreeStrategy,
                "_run_git",
                mock_run_git,
            ),
            pytest.raises(WorkspaceMergeError),
        ):
            await strategy.merge_workspace(workspace=ws)

    @pytest.mark.unit
    async def test_merge_abort_failure_raises(self) -> None:
        """Merge raises WorkspaceMergeError when abort fails."""
        strategy = _make_strategy()
        ws = make_workspace()
        strategy._active_workspaces[ws.workspace_id] = ws

        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git,
            side_effect=[
                (0, "", ""),  # checkout
                (0, "pre123", ""),  # rev-parse HEAD (pre-merge)
                (1, "", "CONFLICT"),  # merge fails
                (0, "src/a.py\n", ""),  # diff --name-only
                (1, "", "error: abort failed"),  # merge --abort fails
            ],
        )

        with (
            patch.object(
                PlannerWorktreeStrategy,
                "_run_git",
                mock_run_git,
            ),
            pytest.raises(WorkspaceMergeError, match="abort"),
        ):
            await strategy.merge_workspace(workspace=ws)

    @pytest.mark.unit
    async def test_merge_revparse_failure_raises(self) -> None:
        """When rev-parse fails, WorkspaceMergeError is raised."""
        strategy = _make_strategy()
        ws = make_workspace()
        strategy._active_workspaces[ws.workspace_id] = ws

        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git,
            side_effect=[
                (0, "", ""),  # checkout
                (0, "pre123", ""),  # rev-parse HEAD (pre-merge)
                (0, "", ""),  # merge
                (1, "", "error: not a valid ref"),  # rev-parse fails
            ],
        )

        with (
            patch.object(
                PlannerWorktreeStrategy,
                "_run_git",
                mock_run_git,
            ),
            pytest.raises(WorkspaceMergeError, match="commit SHA"),
        ):
            await strategy.merge_workspace(workspace=ws)


# ---------------------------------------------------------------------------
# teardown_workspace
# ---------------------------------------------------------------------------


class TestTeardownWorkspace:
    """Tests for teardown_workspace method."""

    @pytest.mark.unit
    async def test_teardown_removes_worktree_and_branch(self) -> None:
        """Teardown removes worktree, deletes branch, unregisters."""
        strategy = _make_strategy()
        ws = make_workspace()
        strategy._active_workspaces[ws.workspace_id] = ws

        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git, return_value=(0, "", "")
        )

        with patch.object(
            PlannerWorktreeStrategy,
            "_run_git",
            mock_run_git,
        ):
            await strategy.teardown_workspace(workspace=ws)

        assert mock_run_git.call_count == 2
        assert ws.workspace_id not in strategy._active_workspaces

    @pytest.mark.unit
    async def test_teardown_worktree_failure_still_deletes_branch(
        self,
    ) -> None:
        """Worktree removal failure still attempts branch deletion."""
        strategy = _make_strategy()
        ws = make_workspace()
        strategy._active_workspaces[ws.workspace_id] = ws

        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git,
            side_effect=[
                (1, "", "error: cannot remove"),  # worktree fails
                (0, "", ""),  # branch -D succeeds
            ],
        )

        with (
            patch.object(
                PlannerWorktreeStrategy,
                "_run_git",
                mock_run_git,
            ),
            pytest.raises(WorkspaceCleanupError, match="worktree remove"),
        ):
            await strategy.teardown_workspace(workspace=ws)

        # Both operations attempted, workspace unregistered
        assert mock_run_git.call_count == 2
        assert ws.workspace_id not in strategy._active_workspaces

    @pytest.mark.unit
    async def test_teardown_branch_failure_raises(self) -> None:
        """Branch deletion failure raises after worktree succeeds."""
        strategy = _make_strategy()
        ws = make_workspace()
        strategy._active_workspaces[ws.workspace_id] = ws

        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git,
            side_effect=[
                (0, "", ""),  # worktree remove succeeds
                (1, "", "error: branch not found"),  # branch -D fails
            ],
        )

        with (
            patch.object(
                PlannerWorktreeStrategy,
                "_run_git",
                mock_run_git,
            ),
            pytest.raises(WorkspaceCleanupError, match="branch delete"),
        ):
            await strategy.teardown_workspace(workspace=ws)

        # Workspace still unregistered to prevent capacity leak
        assert ws.workspace_id not in strategy._active_workspaces


# ---------------------------------------------------------------------------
# list_active_workspaces
# ---------------------------------------------------------------------------


class TestListActiveWorkspaces:
    """Tests for list_active_workspaces method."""

    @pytest.mark.unit
    async def test_empty_initially(self) -> None:
        """No active workspaces at start."""
        strategy = _make_strategy()
        result = await strategy.list_active_workspaces()
        assert result == ()

    @pytest.mark.unit
    async def test_returns_registered_workspaces(self) -> None:
        """Returns all registered workspaces as a tuple."""
        strategy = _make_strategy()
        ws1 = make_workspace(workspace_id="ws-1")
        ws2 = make_workspace(workspace_id="ws-2")
        strategy._active_workspaces["ws-1"] = ws1
        strategy._active_workspaces["ws-2"] = ws2

        result = await strategy.list_active_workspaces()
        assert len(result) == 2
        ids = {w.workspace_id for w in result}
        assert ids == {"ws-1", "ws-2"}


# ---------------------------------------------------------------------------
# Concurrent setup
# ---------------------------------------------------------------------------


class TestConcurrentSetup:
    """Tests for concurrent workspace setup via lock."""

    @pytest.mark.unit
    async def test_concurrent_setup_respects_limit(self) -> None:
        """Two concurrent setups at limit=1: one succeeds, one fails."""
        strategy = _make_strategy(
            config=_make_config(
                max_concurrent_worktrees=1,
            )
        )

        # Events ensure deterministic overlap: task-1 signals when it
        # holds the semaphore, task-2 only starts after that signal.
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        async def mock_git(
            self_: PlannerWorktreeStrategy,
            *args: str,
            **kwargs: object,
        ) -> tuple[int, str, str]:
            # Accept any keyword (``cwd`` / ``log_event`` / ...) so the
            # mock stays robust to ``_run_git`` signature changes.
            first_entered.set()
            await release_first.wait()
            return (0, "", "")

        results: list[object] = []

        async def setup_one(task_id: str) -> None:
            try:
                ws = await strategy.setup_workspace(
                    request=_make_request(task_id=task_id),
                )
                results.append(ws)
            except Exception as exc:
                results.append(exc)

        with patch.object(
            PlannerWorktreeStrategy,
            "_run_git",
            side_effect=mock_git,
        ):
            t1 = asyncio.create_task(setup_one("task-1"))
            await first_entered.wait()
            t2 = asyncio.create_task(setup_one("task-2"))
            release_first.set()
            await asyncio.gather(t1, t2)

        successes = [r for r in results if isinstance(r, Workspace)]
        failures = [r for r in results if isinstance(r, WorkspaceLimitError)]
        assert len(successes) == 1
        assert len(failures) == 1


# ---------------------------------------------------------------------------
# _collect_conflicts
# ---------------------------------------------------------------------------


class TestCollectConflicts:
    """Tests for _collect_conflicts method."""

    @pytest.mark.unit
    async def test_diff_failure_raises(self) -> None:
        """When git diff fails, WorkspaceMergeError is raised."""
        strategy = _make_strategy()
        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git,
            return_value=(1, "", "error: diff failed"),
        )

        with (
            patch.object(
                PlannerWorktreeStrategy,
                "_run_git",
                mock_run_git,
            ),
            pytest.raises(WorkspaceMergeError, match="conflict details"),
        ):
            await strategy._collect_conflicts(strategy._repo_root)

    @pytest.mark.unit
    async def test_empty_stdout_returns_empty(self) -> None:
        """When diff returns no files, returns empty tuple."""
        strategy = _make_strategy()
        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git, return_value=(0, "", "")
        )

        with patch.object(
            PlannerWorktreeStrategy,
            "_run_git",
            mock_run_git,
        ):
            result = await strategy._collect_conflicts(strategy._repo_root)

        assert result == ()


# ---------------------------------------------------------------------------
# Context-specific exception types for _validate_git_ref
# ---------------------------------------------------------------------------


class TestValidateGitRefContextExceptions:
    """Verify _validate_git_ref raises the right exception per context."""

    @pytest.mark.unit
    async def test_merge_unsafe_ref_raises_merge_error(self) -> None:
        """Unsafe branch_name in merge context raises WorkspaceMergeError."""
        strategy = _make_strategy()
        ws = make_workspace(branch_name="--malicious")
        strategy._active_workspaces[ws.workspace_id] = ws

        with pytest.raises(WorkspaceMergeError, match="Unsafe"):
            await strategy.merge_workspace(workspace=ws)

    @pytest.mark.unit
    async def test_teardown_unsafe_ref_raises_cleanup_error(self) -> None:
        """Unsafe branch_name in teardown context raises WorkspaceCleanupError."""
        strategy = _make_strategy()
        ws = make_workspace(branch_name="--malicious")
        strategy._active_workspaces[ws.workspace_id] = ws

        with pytest.raises(WorkspaceCleanupError, match="Unsafe"):
            await strategy.teardown_workspace(workspace=ws)

    @pytest.mark.unit
    async def test_merge_path_traversal_base_raises_merge_error(self) -> None:
        """Path traversal base_branch in merge context raises WorkspaceMergeError."""
        strategy = _make_strategy()
        ws = make_workspace(branch_name="valid/branch", base_branch="../escape")
        strategy._active_workspaces[ws.workspace_id] = ws

        with pytest.raises(WorkspaceMergeError, match="Unsafe"):
            await strategy.merge_workspace(workspace=ws)

    @pytest.mark.unit
    async def test_teardown_path_traversal_raises_cleanup_error(self) -> None:
        """Path traversal branch_name in teardown raises WorkspaceCleanupError."""
        strategy = _make_strategy()
        ws = make_workspace(branch_name="../escape")
        strategy._active_workspaces[ws.workspace_id] = ws

        with pytest.raises(WorkspaceCleanupError, match="Unsafe"):
            await strategy.teardown_workspace(workspace=ws)

    @pytest.mark.unit
    async def test_setup_unsafe_ref_raises_setup_error(self) -> None:
        """Unsafe task_id in setup context raises WorkspaceSetupError."""
        strategy = _make_strategy()
        request = WorkspaceRequest(
            task_id="--inject",
            agent_id="agent-1",
            base_branch="main",
        )

        with pytest.raises(WorkspaceSetupError, match="Unsafe"):
            await strategy.setup_workspace(request=request)


# ---------------------------------------------------------------------------
# CancelledError subprocess cleanup
# ---------------------------------------------------------------------------


class TestRunGitCancelledError:
    """Verify _run_git kills subprocess on CancelledError."""

    @pytest.mark.unit
    async def test_cancelled_error_kills_process(self) -> None:
        """CancelledError during wait_for kills the subprocess."""
        import warnings

        strategy = _make_strategy()

        kill_called = False
        wait_called = False

        class FakeProc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                # This will be wrapped in wait_for; we never
                # actually reach it because we patch wait_for
                return (b"", b"")  # pragma: no cover

            def kill(self) -> None:
                nonlocal kill_called
                kill_called = True

            async def wait(self) -> int:
                nonlocal wait_called
                wait_called = True
                return 0

        async def mock_wait_for(
            coro: object,
            **_kwargs: object,
        ) -> object:
            if asyncio.iscoroutine(coro):
                coro.close()
            raise asyncio.CancelledError

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", pytest.PytestUnraisableExceptionWarning)
            with (
                patch(
                    "asyncio.create_subprocess_exec",
                    return_value=FakeProc(),
                ),
                patch(
                    "synthorg.engine.workspace.git_worktree.asyncio.wait_for",
                    side_effect=mock_wait_for,
                ),
                pytest.raises(asyncio.CancelledError),
            ):
                await strategy._run_git("status")

        assert kill_called
        assert wait_called


# ---------------------------------------------------------------------------
# _validate_file_path (module-level)
# ---------------------------------------------------------------------------


class TestValidateFilePath:
    """Tests for the _validate_file_path module-level function."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("", False),
            ("-malicious", False),
            ("src/../etc/passwd", False),
            ("/etc/passwd", False),
            ("C:/Windows/system32", False),
            ("src/main.py", True),
            ("src/my file.py", True),
            ("src/main;rm -rf.py", False),
        ],
        ids=[
            "empty",
            "dash_prefix",
            "traversal",
            "absolute_unix",
            "absolute_windows",
            "valid_relative",
            "spaces",
            "special_chars",
        ],
    )
    def test_validate_file_path(self, path: str, expected: bool) -> None:
        """Validate file path safety checks."""
        assert _validate_file_path(path) is expected


# ---------------------------------------------------------------------------
# get_merge_base (standalone function)
# ---------------------------------------------------------------------------


class TestGetMergeBase:
    """Tests for get_merge_base standalone function."""

    @pytest.mark.unit
    async def test_success_returns_sha(self) -> None:
        """Returns stripped SHA when git merge-base succeeds."""
        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git,
            return_value=(0, "abc123def\n", ""),
        )

        result = await get_merge_base(mock_run_git, "sha_a", "refs/heads/branch")

        assert result == "abc123def"
        mock_run_git.assert_awaited_once_with(
            "merge-base",
            "sha_a",
            "refs/heads/branch",
            log_event=("workspace.semantic.analysis.failed"),
        )

    @pytest.mark.unit
    async def test_failure_returns_empty(self) -> None:
        """Returns empty string and logs warning on failure."""
        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git,
            return_value=(1, "", "not a valid ref"),
        )

        result = await get_merge_base(mock_run_git, "bad_sha", "refs/heads/gone")

        assert result == ""


# ---------------------------------------------------------------------------
# get_changed_files (standalone function)
# ---------------------------------------------------------------------------


class TestGetChangedFiles:
    """Tests for get_changed_files standalone function."""

    @pytest.mark.unit
    async def test_success_returns_file_paths(self) -> None:
        """Returns tuple of file paths from git diff output."""
        stdout = "src/main.py\nsrc/utils.py\n"
        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git,
            return_value=(0, stdout, ""),
        )

        result = await get_changed_files(mock_run_git, "base_sha", "merge_sha")

        assert result == ("src/main.py", "src/utils.py")

    @pytest.mark.unit
    async def test_failure_returns_empty(self) -> None:
        """Returns empty tuple and logs warning on failure."""
        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git,
            return_value=(1, "", "error: bad revision"),
        )

        result = await get_changed_files(mock_run_git, "bad", "sha")

        assert result == ()

    @pytest.mark.unit
    async def test_empty_stdout_returns_empty(self) -> None:
        """Returns empty tuple when diff produces no output."""
        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git,
            return_value=(0, "", ""),
        )

        result = await get_changed_files(mock_run_git, "sha_a", "sha_b")

        assert result == ()

    @pytest.mark.unit
    async def test_filters_unsafe_paths(self) -> None:
        """Unsafe file paths are excluded from results."""
        stdout = "src/good.py\n--malicious\n../escape.py\nsrc/also-good.py\n"
        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git,
            return_value=(0, stdout, ""),
        )

        result = await get_changed_files(mock_run_git, "sha_a", "sha_b")

        assert result == ("src/good.py", "src/also-good.py")


# ---------------------------------------------------------------------------
# get_base_sources (standalone function)
# ---------------------------------------------------------------------------


class TestGetBaseSources:
    """Tests for get_base_sources standalone function."""

    @pytest.mark.unit
    async def test_success_returns_file_contents(self) -> None:
        """Returns dict mapping file path to content."""

        async def mock_run_git(
            *args: str,
            cmd_timeout: float = 60.0,
            log_event: str = "",
        ) -> tuple[int, str, str]:
            # args = ("show", "<sha>:<file>")
            if args[0] == "show":
                if "main.py" in args[1]:
                    return (0, "print('hello')", "")
                if "utils.py" in args[1]:
                    return (0, "def foo(): ...", "")
            return (1, "", "error")  # pragma: no cover

        result = await get_base_sources(
            mock_run_git,
            "abc123",
            ("src/main.py", "src/utils.py"),
            concurrency=2,
        )

        assert result == {
            "src/main.py": "print('hello')",
            "src/utils.py": "def foo(): ...",
        }

    @pytest.mark.unit
    async def test_skips_failed_files(self) -> None:
        """Files that fail git show are omitted (logged at debug)."""

        async def mock_run_git(
            *args: str,
            cmd_timeout: float = 60.0,
            log_event: str = "",
        ) -> tuple[int, str, str]:
            if args[0] == "show":
                if "good.py" in args[1]:
                    return (0, "content", "")
                return (1, "", "not found")
            return (1, "", "error")  # pragma: no cover

        result = await get_base_sources(
            mock_run_git,
            "abc123",
            ("src/good.py", "src/deleted.py"),
            concurrency=2,
        )

        assert "src/good.py" in result
        assert "src/deleted.py" not in result

    @pytest.mark.unit
    async def test_skips_unsafe_file_paths(self) -> None:
        """Unsafe file paths are skipped (logged at warning)."""
        mock_run_git = AsyncMock(
            spec=PlannerWorktreeStrategy._run_git,
            return_value=(0, "content", ""),
        )

        result = await get_base_sources(
            mock_run_git,
            "abc123",
            ("--malicious", "src/good.py"),
            concurrency=2,
        )

        # Only the safe path should be fetched
        assert "--malicious" not in result
        assert "src/good.py" in result

    @pytest.mark.unit
    async def test_missing_concurrency_and_semaphore_raises(self) -> None:
        """HYG-2 contract: one of concurrency= or semaphore= is required.

        The previous silent default (concurrency=10) was removed so the
        function cannot drift from
        ``SemanticAnalysisConfig.git_concurrency``.  A caller that
        passes neither must fail loud with a message pointing at the
        config field.
        """
        mock_run_git = AsyncMock(spec=PlannerWorktreeStrategy._run_git)
        with pytest.raises(
            ValueError,
            match=r"get_base_sources requires either concurrency",
        ):
            await get_base_sources(
                mock_run_git,
                "abc123",
                ("src/main.py",),
            )

    @pytest.mark.unit
    @pytest.mark.parametrize("concurrency", [0, -1])
    async def test_non_positive_concurrency_raises(
        self,
        concurrency: int,
    ) -> None:
        """Non-positive concurrency must fail loud.

        ``asyncio.Semaphore(0)`` deadlocks every fetch task; negative
        values raise at Semaphore construction but the error message
        is opaque.  Validate at call time instead.
        """
        mock_run_git = AsyncMock(spec=PlannerWorktreeStrategy._run_git)
        with pytest.raises(
            ValueError,
            match=r"concurrency must be > 0",
        ):
            await get_base_sources(
                mock_run_git,
                "abc123",
                ("src/main.py",),
                concurrency=concurrency,
            )


# ---------------------------------------------------------------------------
# _run_semantic_analysis
# ---------------------------------------------------------------------------


class TestRunSemanticAnalysis:
    """Tests for _run_semantic_analysis method."""

    @pytest.mark.unit
    async def test_returns_empty_when_no_analyzer(self) -> None:
        """Returns () when no semantic_analyzer is configured."""
        strategy = _make_strategy(semantic_analyzer=None)
        ws = make_workspace()

        result = await strategy._run_semantic_analysis(
            workspace=ws,
            pre_merge_sha="abc123",
            merge_sha="def456",
        )

        assert result == ()

    @pytest.mark.unit
    async def test_returns_empty_when_pre_merge_sha_empty(
        self,
    ) -> None:
        """Returns () when pre_merge_sha is empty."""
        mock_analyzer = AsyncMock(spec=SemanticAnalyzer)
        strategy = _make_strategy(
            config=PlannerWorktreesConfig(
                semantic_analysis=SemanticAnalysisConfig(
                    enabled=True,
                ),
            ),
            semantic_analyzer=mock_analyzer,
        )
        ws = make_workspace()

        result = await strategy._run_semantic_analysis(
            workspace=ws,
            pre_merge_sha="",
            merge_sha="def456",
        )

        assert result == ()

    @pytest.mark.unit
    async def test_returns_empty_when_disabled(self) -> None:
        """Returns () when semantic_analysis.enabled is False."""
        mock_analyzer = AsyncMock(spec=SemanticAnalyzer)
        strategy = _make_strategy(
            config=PlannerWorktreesConfig(
                semantic_analysis=SemanticAnalysisConfig(
                    enabled=False,
                ),
            ),
            semantic_analyzer=mock_analyzer,
        )
        ws = make_workspace()

        result = await strategy._run_semantic_analysis(
            workspace=ws,
            pre_merge_sha="abc123",
            merge_sha="def456",
        )

        assert result == ()

    @pytest.mark.unit
    async def test_delegates_to_run_semantic_analysis(
        self,
    ) -> None:
        """Delegates to semantic_git_ops.run_semantic_analysis."""
        mock_analyzer = AsyncMock(spec=SemanticAnalyzer)
        strategy = _make_strategy(
            config=PlannerWorktreesConfig(
                semantic_analysis=SemanticAnalysisConfig(
                    enabled=True,
                ),
            ),
            semantic_analyzer=mock_analyzer,
        )
        ws = make_workspace()

        mock_fn = AsyncMock(spec=run_semantic_analysis, return_value=())
        with patch(
            "synthorg.engine.workspace.git_worktree.run_semantic_analysis",
            mock_fn,
        ):
            result = await strategy._run_semantic_analysis(
                workspace=ws,
                pre_merge_sha="abc123",
                merge_sha="def456",
            )

        assert result == ()
        mock_fn.assert_awaited_once_with(
            run_git=strategy._run_git,
            config=strategy._config.semantic_analysis,
            analyzer=mock_analyzer,
            workspace=ws,
            pre_merge_sha="abc123",
            merge_sha="def456",
        )

    @pytest.mark.unit
    async def test_logs_warning_when_conflicts_found(
        self,
    ) -> None:
        """Logs a warning when semantic conflicts are detected."""
        from synthorg.core.enums import ConflictType
        from synthorg.engine.workspace.models import MergeConflict

        conflict = MergeConflict(
            file_path="src/main.py",
            conflict_type=ConflictType.SEMANTIC,
            description="removed reference",
        )
        mock_analyzer = AsyncMock(spec=SemanticAnalyzer)
        strategy = _make_strategy(
            config=PlannerWorktreesConfig(
                semantic_analysis=SemanticAnalysisConfig(
                    enabled=True,
                ),
            ),
            semantic_analyzer=mock_analyzer,
        )
        ws = make_workspace()

        mock_fn = AsyncMock(spec=run_semantic_analysis, return_value=(conflict,))
        with patch(
            "synthorg.engine.workspace.git_worktree.run_semantic_analysis",
            mock_fn,
        ):
            result = await strategy._run_semantic_analysis(
                workspace=ws,
                pre_merge_sha="abc123",
                merge_sha="def456",
            )

        assert len(result) == 1
        assert result[0].file_path == "src/main.py"


# ---------------------------------------------------------------------------
# _do_analysis (via _run_semantic_analysis)
# ---------------------------------------------------------------------------


class TestDoSemanticAnalysis:
    """Tests for the semantic analysis pipeline via _run_semantic_analysis."""

    @pytest.mark.unit
    async def test_catches_non_cancelled_exception(self) -> None:
        """Non-CancelledError exceptions are caught, returns ()."""
        mock_analyzer = AsyncMock(spec=SemanticAnalyzer)
        strategy = _make_strategy(
            config=PlannerWorktreesConfig(
                semantic_analysis=SemanticAnalysisConfig(
                    enabled=True,
                ),
            ),
            semantic_analyzer=mock_analyzer,
        )
        ws = make_workspace()

        # Make get_merge_base raise a RuntimeError
        mock_base = AsyncMock(
            spec=get_merge_base,
            side_effect=RuntimeError("git crashed"),
        )
        with patch(
            "synthorg.engine.workspace.semantic_git_ops.get_merge_base",
            mock_base,
        ):
            result = await strategy._run_semantic_analysis(
                workspace=ws,
                pre_merge_sha="abc123",
                merge_sha="def456",
            )

        assert result == ()

    @pytest.mark.unit
    async def test_reraises_cancelled_error(self) -> None:
        """CancelledError is re-raised, not swallowed."""
        mock_analyzer = AsyncMock(spec=SemanticAnalyzer)
        strategy = _make_strategy(
            config=PlannerWorktreesConfig(
                semantic_analysis=SemanticAnalysisConfig(
                    enabled=True,
                ),
            ),
            semantic_analyzer=mock_analyzer,
        )
        ws = make_workspace()

        mock_base = AsyncMock(
            spec=get_merge_base,
            side_effect=asyncio.CancelledError,
        )
        with (
            patch(
                "synthorg.engine.workspace.semantic_git_ops.get_merge_base",
                mock_base,
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await strategy._run_semantic_analysis(
                workspace=ws,
                pre_merge_sha="abc123",
                merge_sha="def456",
            )

    @pytest.mark.unit
    async def test_pre_filters_files_by_extension(self) -> None:
        """Only files matching configured extensions are analyzed."""

        mock_analyzer = AsyncMock(spec=SemanticAnalyzer)
        mock_analyzer.analyze = AsyncMock(return_value=())
        strategy = _make_strategy(
            config=PlannerWorktreesConfig(
                semantic_analysis=SemanticAnalysisConfig(
                    enabled=True,
                    file_extensions=(".py",),
                ),
            ),
            semantic_analyzer=mock_analyzer,
        )
        ws = make_workspace()

        mock_base = AsyncMock(spec=get_merge_base, return_value="abc123")
        mock_changed = AsyncMock(
            spec=get_changed_files,
            return_value=(
                "src/main.py",
                "README.txt",
                "src/utils.py",
            ),
        )
        # get_base_sources is called twice (base + merged)
        mock_sources = AsyncMock(
            spec=get_base_sources,
            return_value={
                "src/main.py": "code1",
                "src/utils.py": "code2",
            },
        )

        with (
            patch(
                "synthorg.engine.workspace.semantic_git_ops.get_merge_base",
                mock_base,
            ),
            patch(
                "synthorg.engine.workspace.semantic_git_ops.get_changed_files",
                mock_changed,
            ),
            patch(
                "synthorg.engine.workspace.semantic_git_ops.get_base_sources",
                mock_sources,
            ),
        ):
            await strategy._run_semantic_analysis(
                workspace=ws,
                pre_merge_sha="abc123",
                merge_sha="def456",
            )

        # get_base_sources called twice (base + merged via _fetch_sources)
        assert mock_sources.await_count == 2
        for call in mock_sources.call_args_list:
            files_arg = call[0][2]
            assert "README.txt" not in files_arg
            assert "src/main.py" in files_arg
            assert "src/utils.py" in files_arg

        mock_analyzer.analyze.assert_awaited_once()
        analyze_kwargs = mock_analyzer.analyze.call_args[1]
        assert "README.txt" not in analyze_kwargs["changed_files"]
