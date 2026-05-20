"""Unit tests for the coordinator-owned serial merge/push queue."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests._shared import FakeClock, mock_of

from synthorg.core.enums import ConflictType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import WorkspaceMergeError, WorkspacePushError
from synthorg.engine.workspace.git_backend import GitBackend, PushResult
from synthorg.engine.workspace.models import (
    MergeConflict,
    MergeResult,
    Workspace,
)
from synthorg.engine.workspace.protocol import WorkspaceIsolationStrategy
from synthorg.engine.workspace.push_queue import PushQueueCoordinator

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
