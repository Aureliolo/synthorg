"""Unit tests for the coordinator-owned serial merge/push queue."""

import asyncio
import contextlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import WorkspaceMergeError, WorkspacePushError
from synthorg.engine.workspace.enums import ConflictType
from synthorg.engine.workspace.git_backend import GitBackend, PushResult
from synthorg.engine.workspace.models import (
    MergeConflict,
    MergeResult,
    Workspace,
)
from synthorg.engine.workspace.protocol import WorkspaceIsolationStrategy
from synthorg.engine.workspace.push_queue import PushQueueCoordinator
from tests._shared import FakeClock, mock_of

pytestmark = pytest.mark.unit


def _workspace(wid: str, branch: str) -> Workspace:
    return Workspace(
        workspace_id=NotBlankStr(wid),
        task_id=NotBlankStr(f"task-{wid}"),
        agent_id=NotBlankStr(f"agent-{wid}"),
        branch_name=NotBlankStr(branch),
        worktree_path=NotBlankStr(f"/ws/{wid}"),
        base_branch=NotBlankStr("main"),
        created_at=datetime(2026, 5, 19, tzinfo=UTC),
    )


def _ok_merge(wid: str, branch: str) -> MergeResult:
    return MergeResult(
        workspace_id=NotBlankStr(wid),
        branch_name=NotBlankStr(branch),
        success=True,
        merged_commit_sha=NotBlankStr("deadbee"),
        duration_seconds=0.0,
    )


def _conflicted_merge(wid: str, branch: str) -> MergeResult:
    return MergeResult(
        workspace_id=NotBlankStr(wid),
        branch_name=NotBlankStr(branch),
        success=False,
        conflicts=(
            MergeConflict(
                file_path=NotBlankStr("a.py"),
                conflict_type=ConflictType.TEXTUAL,
            ),
        ),
        duration_seconds=0.0,
    )


def _coordinator(
    strategy: WorkspaceIsolationStrategy,
    git_backend: GitBackend,
) -> PushQueueCoordinator:
    return PushQueueCoordinator(
        project_id=NotBlankStr("proj-1"),
        strategy=strategy,
        git_backend=git_backend,
        repo_root=Path("/repo/proj-1"),
        default_branch=NotBlankStr("main"),
        clock=FakeClock(),
    )


class TestPushQueueCoordinator:
    async def test_serialises_merge_then_push_fifo(self) -> None:
        order: list[str] = []

        async def _merge(*, workspace: Workspace) -> MergeResult:
            order.append(f"merge:{workspace.branch_name}")
            await asyncio.sleep(0)
            return _ok_merge(workspace.workspace_id, workspace.branch_name)

        async def _push(**kwargs: object) -> PushResult:
            order.append(f"push:{kwargs['branch']}")
            return PushResult(
                branch=NotBlankStr("main"),
                head_sha=NotBlankStr("deadbee"),
            )

        strategy = mock_of[WorkspaceIsolationStrategy]()
        strategy.merge_workspace.side_effect = _merge
        git_backend = mock_of[GitBackend]()
        git_backend.push.side_effect = _push

        coord = _coordinator(strategy, git_backend)
        await coord.start()
        try:
            r1, r2 = await asyncio.gather(
                coord.enqueue_merge_push(workspace=_workspace("w1", "b1")),
                coord.enqueue_merge_push(workspace=_workspace("w2", "b2")),
            )
        finally:
            await coord.stop()

        assert r1.success
        assert r2.success
        # Each merge fully precedes the next merge (serialised, not interleaved).
        assert order == [
            "merge:b1",
            "push:main",
            "merge:b2",
            "push:main",
        ]

    async def test_merge_error_propagates_queue_continues(self) -> None:
        strategy = mock_of[WorkspaceIsolationStrategy]()
        strategy.merge_workspace.side_effect = [
            WorkspaceMergeError("conflict abort"),
            _ok_merge("w2", "b2"),
        ]
        git_backend = mock_of[GitBackend]()
        git_backend.push.return_value = PushResult(
            branch=NotBlankStr("main"),
            head_sha=NotBlankStr("deadbee"),
        )
        coord = _coordinator(strategy, git_backend)
        await coord.start()
        try:
            with pytest.raises(WorkspaceMergeError):
                await coord.enqueue_merge_push(workspace=_workspace("w1", "b1"))
            r2 = await coord.enqueue_merge_push(workspace=_workspace("w2", "b2"))
        finally:
            await coord.stop()
        assert r2.success

    async def test_stop_drain_timeout_marks_unrestartable(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A worker hung on a never-returning git operation must not hold
        # teardown forever: stop() bounds the drain, cancels the worker,
        # and marks the coordinator unrestartable so a racing start() cannot
        # spawn a second worker on a stale queue.
        from synthorg.engine.errors import PushQueueUnrestartableError
        from synthorg.engine.workspace import push_queue as pq_mod

        monkeypatch.setattr(pq_mod, "_DRAIN_TIMEOUT_SECONDS", 0.05)

        entered = asyncio.Event()
        release = asyncio.Event()  # never set -> the merge hangs

        async def _merge(*, workspace: Workspace) -> MergeResult:
            entered.set()
            await release.wait()
            return _ok_merge(workspace.workspace_id, workspace.branch_name)

        strategy = mock_of[WorkspaceIsolationStrategy]()
        strategy.merge_workspace.side_effect = _merge
        git_backend = mock_of[GitBackend]()

        coord = _coordinator(strategy, git_backend)
        await coord.start()
        enqueue_task = asyncio.create_task(
            coord.enqueue_merge_push(workspace=_workspace("w1", "b1")),
        )
        # Wait until the worker is actually blocked inside the merge.
        await asyncio.wait_for(entered.wait(), timeout=1.0)

        # Drain exceeds the (patched) deadline -> times out, does not hang.
        await coord.stop()

        with pytest.raises(PushQueueUnrestartableError):
            await coord.start()

        # The in-flight caller is abandoned with the hard teardown; cancel
        # its task so the test loop does not leak it.
        enqueue_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await enqueue_task

    async def test_critical_error_completes_future_before_worker_dies(self) -> None:
        # A critical error (MemoryError) raised inside the worker must
        # resolve the dequeued item's future before the worker re-raises
        # and dies; otherwise the awaiting caller hangs forever (the
        # outer drain loop only rescues items still on the queue).
        strategy = mock_of[WorkspaceIsolationStrategy]()
        strategy.merge_workspace.side_effect = MemoryError("oom")
        git_backend = mock_of[GitBackend]()
        coord = _coordinator(strategy, git_backend)
        await coord.start()
        try:
            with pytest.raises(MemoryError):
                await asyncio.wait_for(
                    coord.enqueue_merge_push(workspace=_workspace("w1", "b1")),
                    timeout=5.0,
                )
        finally:
            with contextlib.suppress(MemoryError):
                await coord.stop()

    async def test_push_failure_raises_workspace_push_error(self) -> None:
        strategy = mock_of[WorkspaceIsolationStrategy]()
        strategy.merge_workspace.return_value = _ok_merge("w1", "b1")
        git_backend = mock_of[GitBackend]()
        git_backend.push.side_effect = RuntimeError("remote rejected")
        coord = _coordinator(strategy, git_backend)
        await coord.start()
        try:
            with pytest.raises(WorkspacePushError):
                await coord.enqueue_merge_push(workspace=_workspace("w1", "b1"))
        finally:
            await coord.stop()

    async def test_conflicted_merge_not_pushed(self) -> None:
        strategy = mock_of[WorkspaceIsolationStrategy]()
        strategy.merge_workspace.return_value = _conflicted_merge("w1", "b1")
        git_backend = mock_of[GitBackend]()
        coord = _coordinator(strategy, git_backend)
        await coord.start()
        try:
            result = await coord.enqueue_merge_push(workspace=_workspace("w1", "b1"))
        finally:
            await coord.stop()
        assert result.success is False
        git_backend.push.assert_not_called()

    async def test_stop_is_idempotent_and_clean(self) -> None:
        strategy = mock_of[WorkspaceIsolationStrategy]()
        git_backend = mock_of[GitBackend]()
        coord = _coordinator(strategy, git_backend)
        await coord.start()
        await coord.stop()
        await coord.stop()
