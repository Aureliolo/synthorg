"""Unit tests for ``WorkspaceIsolationService`` push-queue integration."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests._shared import FakeClock, mock_of

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.config import WorkspaceIsolationConfig
from synthorg.engine.workspace.git_backend import GitBackend, PushResult
from synthorg.engine.workspace.models import MergeResult, Workspace
from synthorg.engine.workspace.protocol import WorkspaceIsolationStrategy
from synthorg.engine.workspace.service import WorkspaceIsolationService

pytestmark = pytest.mark.unit


def _workspace() -> Workspace:
    return Workspace(
        workspace_id=NotBlankStr("w1"),
        task_id=NotBlankStr("t1"),
        agent_id=NotBlankStr("a1"),
        branch_name=NotBlankStr("feature"),
        worktree_path=NotBlankStr("/ws/w1"),
        base_branch=NotBlankStr("main"),
        created_at=datetime(2026, 5, 19, tzinfo=UTC),
    )


def _ok_merge() -> MergeResult:
    return MergeResult(
        workspace_id=NotBlankStr("w1"),
        branch_name=NotBlankStr("feature"),
        success=True,
        merged_commit_sha=NotBlankStr("deadbee"),
        duration_seconds=0.0,
    )


class TestMergeWorkspaceWithPush:
    async def test_no_backend_delegates_to_strategy(self) -> None:
        strategy = mock_of[WorkspaceIsolationStrategy]()
        strategy.merge_workspace.return_value = _ok_merge()
        service = WorkspaceIsolationService(
            strategy=strategy,
            config=WorkspaceIsolationConfig(),
            clock=FakeClock(),
        )

        result = await service.merge_workspace_with_push(
            workspace=_workspace(),
            project_id=NotBlankStr("proj-1"),
            repo_root=Path("/repo/proj-1"),
        )

        assert result.success
        strategy.merge_workspace.assert_awaited_once()

    async def test_with_backend_routes_through_queue(self) -> None:
        strategy = mock_of[WorkspaceIsolationStrategy]()
        strategy.merge_workspace.return_value = _ok_merge()
        git_backend = mock_of[GitBackend]()
        git_backend.push.return_value = PushResult(
            branch=NotBlankStr("main"),
            head_sha=NotBlankStr("deadbee"),
        )
        service = WorkspaceIsolationService(
            strategy=strategy,
            config=WorkspaceIsolationConfig(),
            git_backend=git_backend,
            clock=FakeClock(),
        )

        result = await service.merge_workspace_with_push(
            workspace=_workspace(),
            project_id=NotBlankStr("proj-1"),
            repo_root=Path("/repo/proj-1"),
        )

        assert result.success
        git_backend.push.assert_awaited_once()
        await service.shutdown()

    async def test_same_project_reuses_one_queue(self) -> None:
        strategy = mock_of[WorkspaceIsolationStrategy]()
        strategy.merge_workspace.return_value = _ok_merge()
        git_backend = mock_of[GitBackend]()
        git_backend.push.return_value = PushResult(
            branch=NotBlankStr("main"),
            head_sha=NotBlankStr("deadbee"),
        )
        service = WorkspaceIsolationService(
            strategy=strategy,
            config=WorkspaceIsolationConfig(),
            git_backend=git_backend,
            clock=FakeClock(),
        )

        q1 = await service._get_or_create_queue(
            project_id=NotBlankStr("proj-1"),
            repo_root=Path("/repo/proj-1"),
        )
        q2 = await service._get_or_create_queue(
            project_id=NotBlankStr("proj-1"),
            repo_root=Path("/repo/proj-1"),
        )
        assert q1 is q2
        await service.shutdown()
