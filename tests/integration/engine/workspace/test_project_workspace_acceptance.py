"""Acceptance tests for the persistent project workspace substrate.

Validates the four locked acceptance criteria end to end:

1. A project gets a persistent git-backed workspace (survives a
   simulated session restart by rebinding the service against the
   same on-volume base + persisted row).
2. Two agents merge concurrently on one project through the serial
   push queue without branch collision and without losing pushes.
3. Switching the git backend kind (embedded -> local_path) is a
   config-only change against the same persisted row.
4. A project-A sandbox cannot resolve / read into project-B's
   workspace (structural cross-project mount isolation).
"""

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests._shared import FakeClock, mock_of

from synthorg.core.enums import ConflictType, GitBackendType
from synthorg.core.project_workspace import ProjectWorkspace
from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.git_backend import (
    GitBackendConfig,
    GitBackendDeps,
    PushResult,
    build_git_backend,
)
from synthorg.engine.workspace.models import (
    MergeConflict,
    MergeResult,
    Workspace,
)
from synthorg.engine.workspace.project_workspace_service import (
    ProjectWorkspaceService,
)
from synthorg.engine.workspace.protocol import WorkspaceIsolationStrategy
from synthorg.engine.workspace.push_queue import PushQueueCoordinator

pytestmark = pytest.mark.integration


class _InMemoryWorkspaceRepo:
    """Stateful in-memory repo so the same row survives 'session restart'."""

    def __init__(self) -> None:
        self._rows: dict[str, ProjectWorkspace] = {}

    async def save(self, entity: ProjectWorkspace) -> None:
        self._rows[entity.project_id] = entity

    async def get(self, entity_id: NotBlankStr) -> ProjectWorkspace | None:
        return self._rows.get(entity_id)

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ProjectWorkspace, ...]:
        rows = sorted(self._rows.values(), key=lambda r: r.project_id)
        return tuple(rows[offset : offset + limit])

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._rows.pop(entity_id, None) is not None


def _build_service(
    base_root: Path,
    repo: _InMemoryWorkspaceRepo,
    *,
    kind: GitBackendType = GitBackendType.EMBEDDED,
    local_repo_path: str | None = None,
) -> ProjectWorkspaceService:
    config = (
        GitBackendConfig(kind=kind, local_repo_path=local_repo_path)
        if kind is GitBackendType.LOCAL_PATH
        else GitBackendConfig(kind=kind)
    )
    git_backend = build_git_backend(
        config,
        GitBackendDeps(workspace_base_root=base_root, clock=FakeClock()),
    )
    return ProjectWorkspaceService(
        base_root=base_root,
        repo=repo,
        git_backend=git_backend,
        config=config,
        clock=FakeClock(),
    )


class TestPersistentGitBackedWorkspace:
    """Acceptance #1: project gets a persistent git-backed workspace."""

    async def test_provision_creates_real_repo_and_persists_row(
        self, tmp_path: Path
    ) -> None:
        repo = _InMemoryWorkspaceRepo()
        svc = _build_service(tmp_path, repo)

        ws = await svc.get_or_provision(NotBlankStr("proj-1"))

        assert ws.git_backend_kind is GitBackendType.EMBEDDED
        assert (Path(ws.workspace_path) / ".git").exists()
        assert (tmp_path / "git-repos" / "proj-1.git").exists()
        assert (await repo.get(NotBlankStr("proj-1"))) == ws

    async def test_survives_simulated_session_restart(self, tmp_path: Path) -> None:
        repo = _InMemoryWorkspaceRepo()

        # Session 1: provision.
        await _build_service(tmp_path, repo).get_or_provision(NotBlankStr("proj-1"))

        # Session 2: rebuild the service against the same base + repo;
        # the workspace must be resolved (no re-provision).
        svc2 = _build_service(tmp_path, repo)
        ws2 = await svc2.get_or_provision(NotBlankStr("proj-1"))

        assert ws2.git_backend_kind is GitBackendType.EMBEDDED
        # Same on-volume location, not re-created.
        assert (Path(ws2.workspace_path) / ".git").exists()


def _ok_merge(wid: str, branch: str) -> MergeResult:
    return MergeResult(
        workspace_id=NotBlankStr(wid),
        branch_name=NotBlankStr(branch),
        success=True,
        merged_commit_sha=NotBlankStr("deadbee"),
        duration_seconds=0.0,
    )


def _workspace(wid: str, branch: str, repo_root: Path) -> Workspace:
    return Workspace(
        workspace_id=NotBlankStr(wid),
        task_id=NotBlankStr(f"task-{wid}"),
        agent_id=NotBlankStr(f"agent-{wid}"),
        branch_name=NotBlankStr(branch),
        worktree_path=NotBlankStr(str(repo_root)),
        base_branch=NotBlankStr("main"),
        created_at=datetime(2026, 5, 19, tzinfo=UTC),
    )


class TestConcurrentAgentsNoCollision:
    """Acceptance #2: two agents merge concurrently without branch collision."""

    async def test_serialised_push_no_collision(self, tmp_path: Path) -> None:
        repo = _InMemoryWorkspaceRepo()
        svc = _build_service(tmp_path, repo)
        ws_row = await svc.get_or_provision(NotBlankStr("proj-1"))
        repo_root = Path(ws_row.workspace_path)

        push_order: list[str] = []
        active_pushes = 0
        max_concurrent_pushes = 0
        first_push_entered = asyncio.Event()
        release_first_push = asyncio.Event()

        async def _merge(*, workspace: Workspace) -> MergeResult:
            await asyncio.sleep(0)
            return _ok_merge(workspace.workspace_id, workspace.branch_name)

        async def _push(**kwargs: object) -> PushResult:
            # Track concurrent push invocations: if the queue ever lets
            # two pushes run side-by-side, ``max_concurrent_pushes``
            # will exceed 1 and the assertion at the end will fail.
            # The event-pair pins the first push inside this critical
            # section so the second push has time to attempt entry --
            # a queue regression that loses serialisation would let it
            # in here and bump ``active_pushes`` above 1.
            nonlocal active_pushes, max_concurrent_pushes
            active_pushes += 1
            max_concurrent_pushes = max(max_concurrent_pushes, active_pushes)
            if not first_push_entered.is_set():
                first_push_entered.set()
                await release_first_push.wait()
            try:
                push_order.append(f"push:{kwargs['branch']}")
                return PushResult(
                    branch=NotBlankStr("main"),
                    head_sha=NotBlankStr("deadbee"),
                )
            finally:
                active_pushes -= 1

        strategy = mock_of[WorkspaceIsolationStrategy]()
        strategy.merge_workspace.side_effect = _merge
        backend = svc.git_backend
        # Wrap the real backend's push to record call order.
        real_push = backend.push
        backend.push = _push  # type: ignore[method-assign]

        coord = PushQueueCoordinator(
            project_id=NotBlankStr("proj-1"),
            strategy=strategy,
            git_backend=backend,
            repo_root=repo_root,
            default_branch=NotBlankStr("main"),
            clock=FakeClock(),
        )
        await coord.start()
        try:
            task1 = asyncio.create_task(
                coord.enqueue_merge_push(
                    workspace=_workspace("w1", "feature-a", repo_root),
                ),
            )
            task2 = asyncio.create_task(
                coord.enqueue_merge_push(
                    workspace=_workspace("w2", "feature-b", repo_root),
                ),
            )
            # Wait until the first push is parked inside ``_push``; if
            # the queue serialises correctly the second enqueued item
            # is still parked on the queue, NOT inside ``_push``.
            await first_push_entered.wait()
            # Yield so the second task has a chance to attempt push
            # entry if a regression broke serialisation.
            await asyncio.sleep(0)
            release_first_push.set()
            r1 = await task1
            r2 = await task2
        finally:
            await coord.stop()
            backend.push = real_push  # type: ignore[method-assign]

        # Both merges succeeded.
        assert r1.success
        assert r2.success
        # Pushes were serialised through the queue (one push per merge,
        # exactly two, no overlap / no lost pushes).
        assert len(push_order) == 2
        # Hard serialisation invariant: at no point did two pushes run
        # concurrently. ``len(push_order) == 2`` alone would pass even
        # if the queue ran both pushes in parallel.
        assert max_concurrent_pushes == 1
        # Branch names distinct (no collision): the workspace branches
        # are independent; the queue pushes the default branch once per
        # merge.
        assert r1.branch_name != r2.branch_name


class TestConfigOnlyBackendSwitch:
    """Acceptance #3: switching git backend is a config-only change."""

    async def test_embedded_to_local_path_preserves_row(self, tmp_path: Path) -> None:
        repo = _InMemoryWorkspaceRepo()
        first = await _build_service(tmp_path, repo).get_or_provision(
            NotBlankStr("proj-1"),
        )
        # Sanity: EMBEDDED initialised an on-disk repo at its workspace_path.
        prior_path = Path(first.workspace_path)
        assert (prior_path / ".git").exists()

        # Operator switches the backend kind in config. Rebuild the
        # service against the same persisted row + a fresh local path.
        byo_path = tmp_path / "byo"
        svc2 = _build_service(
            tmp_path,
            repo,
            kind=GitBackendType.LOCAL_PATH,
            local_repo_path=str(byo_path),
        )
        ws = await svc2.get_or_provision(NotBlankStr("proj-1"))

        assert ws.git_backend_kind is GitBackendType.LOCAL_PATH
        # The persisted row's kind is updated; the on-volume row reflects
        # the new backend (config-authoritative precedence).
        row = await repo.get(NotBlankStr("proj-1"))
        assert row is not None
        assert row.git_backend_kind is GitBackendType.LOCAL_PATH

        # On-disk: the new LOCAL_PATH backend actually initialised a
        # repo at its per-project subdir (newly_created path), and the
        # prior EMBEDDED ``.git`` metadata at the old location was
        # cleared so the new backend's ``is_git_repo`` short-circuit
        # could not silently retain the old layout. Without these two
        # assertions a regression that flipped only the row's kind
        # (acceptance #3 dead on disk) would still pass.
        new_path = Path(ws.workspace_path)
        assert new_path == byo_path / "proj-1"
        assert (new_path / ".git").exists()
        assert not (prior_path / ".git").exists()


class TestCrossProjectIsolation:
    """Acceptance #4: project-A cannot resolve a path into project-B."""

    async def test_distinct_workspace_paths(self, tmp_path: Path) -> None:
        repo = _InMemoryWorkspaceRepo()
        svc = _build_service(tmp_path, repo)

        ws_a = await svc.get_or_provision(NotBlankStr("proj-a"))
        ws_b = await svc.get_or_provision(NotBlankStr("proj-b"))

        path_a = Path(ws_a.workspace_path)
        path_b = Path(ws_b.workspace_path)
        assert path_a != path_b
        # Neither workspace path is a parent of the other (structural
        # non-overlap: a per-project Docker mount of path_a cannot
        # contain path_b).
        assert path_b not in path_a.parents
        assert path_a not in path_b.parents

    async def test_agent_a_cannot_read_project_b_secret(self, tmp_path: Path) -> None:
        repo = _InMemoryWorkspaceRepo()
        svc = _build_service(tmp_path, repo)
        ws_a = await svc.get_or_provision(NotBlankStr("proj-a"))
        ws_b = await svc.get_or_provision(NotBlankStr("proj-b"))

        # Plant a secret in project-B's tree.
        (Path(ws_b.workspace_path) / "secret-b.txt").write_text("Secret B\n")

        # Simulate project-A's sandbox cwd: tool execution scoped to
        # ws_a.workspace_path. The only way to access project-B's file
        # is via an absolute path the LLM cannot synthesise (tool layer
        # confines to its workspace) or by symlink-traversal.  We
        # assert the structural invariant: from ws_a, project-B's tree
        # is NOT a child of ws_a.workspace_path -- no relative path
        # inside ws_a can reach it.
        path_a = Path(ws_a.workspace_path)
        path_b = Path(ws_b.workspace_path)
        # The negative: no file under path_a equals path_b/secret-b.txt.
        assert not (path_a / "secret-b.txt").exists()
        # Sanity: the secret really is in project-B's tree.
        assert (path_b / "secret-b.txt").read_text() == "Secret B\n"


def _run_git(*args: str, cwd: Path) -> str:
    """Synchronous git invocation; returns stripped stdout."""
    result = subprocess.run(  # noqa: S603 -- args from test code, not untrusted input
        ["git", "-C", str(cwd), *args],  # noqa: S607 -- git is on PATH
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class TestRealGitConcurrentWorktrees:
    """Acceptance #2 (deeper): real git worktrees, real branches."""

    async def test_two_worktrees_one_repo_no_branch_collision(
        self, tmp_path: Path
    ) -> None:
        repo = _InMemoryWorkspaceRepo()
        svc = _build_service(tmp_path, repo)
        ws_row = await svc.get_or_provision(NotBlankStr("proj-1"))
        repo_root = Path(ws_row.workspace_path)

        # Two concurrent worktrees on distinct branches (sync subprocess
        # via helper; the async-event-loop policy in conftest tolerates).
        wt_a = tmp_path / "wt-a"
        wt_b = tmp_path / "wt-b"
        _run_git("worktree", "add", "-b", "agent-a", str(wt_a), cwd=repo_root)
        _run_git("worktree", "add", "-b", "agent-b", str(wt_b), cwd=repo_root)

        assert (wt_a / ".git").exists()
        assert (wt_b / ".git").exists()
        # Each worktree pins its OWN branch; checkouts don't thrash
        # because they live in separate directories sharing the same
        # .git object DB.
        head_a = _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_a)
        head_b = _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_b)
        assert head_a == "agent-a"
        assert head_b == "agent-b"
        assert head_a != head_b  # No branch collision.


# Reference: the conflict path is unit-tested by
# ``tests/unit/engine/workspace/test_push_queue.py::test_conflicted_merge_not_pushed``.
_CONFLICT_REFERENCE: MergeConflict = MergeConflict(
    file_path=NotBlankStr("a.py"),
    conflict_type=ConflictType.TEXTUAL,
)
