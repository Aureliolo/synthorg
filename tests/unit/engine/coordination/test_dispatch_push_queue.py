"""Dispatch merge routes through the per-project push queue.

Covers the ``merge_workspaces`` helper's routing decision (push queue
vs in-memory ``merge_group``) and the dispatch acceptance: a two-
workspace wave on one project routes through ``PushQueueCoordinator``,
asserted via the ``push_queue_events`` metrics counter.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests._shared import FakeClock, mock_of

from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination._dispatch_helpers import merge_workspaces
from synthorg.engine.workspace.config import WorkspaceIsolationConfig
from synthorg.engine.workspace.git_backend import GitBackend, PushResult
from synthorg.engine.workspace.models import (
    MergeResult,
    Workspace,
    WorkspaceGroupResult,
)
from synthorg.engine.workspace.protocol import WorkspaceIsolationStrategy
from synthorg.engine.workspace.service import WorkspaceIsolationService
from synthorg.observability import metrics_hub
from synthorg.observability.prometheus_collector import PrometheusCollector

pytestmark = pytest.mark.unit


def _workspace(wid: str) -> Workspace:
    return Workspace(
        workspace_id=NotBlankStr(wid),
        task_id=NotBlankStr(f"task-{wid}"),
        agent_id=NotBlankStr(f"agent-{wid}"),
        branch_name=NotBlankStr(f"workspace/{wid}"),
        worktree_path=NotBlankStr(f"/ws/{wid}"),
        base_branch=NotBlankStr("main"),
        created_at=datetime(2026, 5, 19, tzinfo=UTC),
    )


def _ok_merge(wid: str) -> MergeResult:
    return MergeResult(
        workspace_id=NotBlankStr(wid),
        branch_name=NotBlankStr(f"workspace/{wid}"),
        success=True,
        merged_commit_sha=NotBlankStr("deadbee"),
        duration_seconds=0.0,
    )


class TestMergeRoutingDecision:
    async def test_routes_via_push_queue_when_project_context_present(self) -> None:
        service = mock_of[WorkspaceIsolationService]()
        service.merge_workspace_with_push.side_effect = lambda *, workspace, **_: (
            _ok_merge(str(workspace.workspace_id))
        )
        workspaces = (_workspace("w1"), _workspace("w2"))

        result, phase = await merge_workspaces(
            service,
            workspaces,
            clock=FakeClock(),
            project_id=NotBlankStr("proj-1"),
            repo_root=Path("/repo/proj-1"),
        )

        assert phase.success
        assert result is not None
        assert len(result.merge_results) == 2
        assert service.merge_workspace_with_push.await_count == 2
        service.merge_group.assert_not_called()

    async def test_falls_back_to_merge_group_without_project_context(self) -> None:
        service = mock_of[WorkspaceIsolationService]()
        service.merge_group.return_value = WorkspaceGroupResult(
            group_id=NotBlankStr("g1"),
            merge_results=(_ok_merge("w1"),),
            duration_seconds=0.0,
        )
        workspaces = (_workspace("w1"),)

        _result, phase = await merge_workspaces(
            service,
            workspaces,
            clock=FakeClock(),
        )

        assert phase.success
        service.merge_group.assert_awaited_once()
        service.merge_workspace_with_push.assert_not_called()

    @pytest.mark.parametrize(
        ("project_id", "repo_root"),
        [
            (NotBlankStr("proj-1"), None),
            (None, Path("/repo/proj-1")),
        ],
        ids=["project-only", "repo-root-only"],
    )
    async def test_partial_context_falls_back_to_merge_group(
        self,
        project_id: NotBlankStr | None,
        repo_root: Path | None,
    ) -> None:
        # Push-queue routing requires BOTH project_id and repo_root; if
        # only one is present the merge falls back to in-memory merge_group.
        service = mock_of[WorkspaceIsolationService]()
        service.merge_group.return_value = WorkspaceGroupResult(
            group_id=NotBlankStr("g1"),
            merge_results=(_ok_merge("w1"),),
            duration_seconds=0.0,
        )

        _result, phase = await merge_workspaces(
            service,
            (_workspace("w1"),),
            clock=FakeClock(),
            project_id=project_id,
            repo_root=repo_root,
        )

        assert phase.success
        service.merge_group.assert_awaited_once()
        service.merge_workspace_with_push.assert_not_called()


class TestTwoWorkspaceWaveRoutesThroughQueue:
    async def test_wave_increments_push_queue_counter(self) -> None:
        # Real WorkspaceIsolationService + push queue; the merge strategy
        # and git backend are mocked so the test stays hermetic. A fake
        # metrics collector captures the push-queue counter so the
        # acceptance ("assertable via a metrics counter") is exercised.
        strategy = mock_of[WorkspaceIsolationStrategy]()
        strategy.merge_workspace.side_effect = lambda *, workspace: _ok_merge(
            str(workspace.workspace_id)
        )
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
        collector = mock_of[PrometheusCollector]()
        metrics_hub.set_active_collector(collector)
        try:
            result, phase = await merge_workspaces(
                service,
                (_workspace("w1"), _workspace("w2")),
                clock=FakeClock(),
                project_id=NotBlankStr("proj-1"),
                repo_root=Path("/repo/proj-1"),
            )
        finally:
            await service.shutdown()
            metrics_hub.clear_active_collector()

        assert phase.success
        assert result is not None
        assert len(result.merge_results) == 2
        outcomes = [
            call.kwargs["outcome"]
            for call in collector.record_push_queue_event.call_args_list
        ]
        assert outcomes.count("enqueued") == 2
        assert outcomes.count("merged") == 2
        # The push queue serialised both merges through the backend.
        assert git_backend.push.await_count == 2
