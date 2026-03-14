"""Tests for WorkspaceIsolationService."""

from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import ConflictType
from synthorg.engine.errors import WorkspaceCleanupError, WorkspaceSetupError
from synthorg.engine.workspace.config import (
    WorkspaceIsolationConfig,
)
from synthorg.engine.workspace.models import (
    MergeConflict,
    MergeResult,
    WorkspaceGroupResult,
    WorkspaceRequest,
)
from synthorg.engine.workspace.service import (
    WorkspaceIsolationService,
)

from .conftest import make_merge_result, make_workspace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    task_id: str = "task-1",
    agent_id: str = "agent-1",
) -> WorkspaceRequest:
    return WorkspaceRequest(task_id=task_id, agent_id=agent_id)


def _make_service(
    *,
    strategy: AsyncMock | None = None,
    config: WorkspaceIsolationConfig | None = None,
) -> WorkspaceIsolationService:
    return WorkspaceIsolationService(
        strategy=strategy or AsyncMock(),
        config=config or WorkspaceIsolationConfig(),
    )


# ---------------------------------------------------------------------------
# setup_group
# ---------------------------------------------------------------------------


class TestSetupGroup:
    """Tests for setup_group method."""

    @pytest.mark.unit
    async def test_setup_group_creates_all(self) -> None:
        """setup_group creates workspace for each request."""
        ws1 = make_workspace(workspace_id="ws-1", task_id="task-1")
        ws2 = make_workspace(workspace_id="ws-2", task_id="task-2")

        mock_strategy = AsyncMock()
        mock_strategy.setup_workspace = AsyncMock(
            side_effect=[ws1, ws2],
        )

        service = _make_service(strategy=mock_strategy)
        result = await service.setup_group(
            requests=(
                _make_request(task_id="task-1"),
                _make_request(task_id="task-2"),
            ),
        )

        assert len(result) == 2
        assert result[0].workspace_id == "ws-1"
        assert result[1].workspace_id == "ws-2"
        assert mock_strategy.setup_workspace.call_count == 2

    @pytest.mark.unit
    async def test_setup_group_empty(self) -> None:
        """setup_group with no requests returns empty tuple."""
        service = _make_service()
        result = await service.setup_group(requests=())
        assert result == ()

    @pytest.mark.unit
    async def test_setup_group_rollback_on_failure(self) -> None:
        """setup_group rolls back created workspaces on failure."""
        ws1 = make_workspace(workspace_id="ws-1", task_id="task-1")

        mock_strategy = AsyncMock()
        mock_strategy.setup_workspace = AsyncMock(
            side_effect=[ws1, WorkspaceSetupError("git failed")],
        )
        mock_strategy.teardown_workspace = AsyncMock()

        service = _make_service(strategy=mock_strategy)

        with pytest.raises(WorkspaceSetupError):
            await service.setup_group(
                requests=(
                    _make_request(task_id="task-1"),
                    _make_request(task_id="task-2"),
                ),
            )

        # ws1 should have been torn down as rollback
        mock_strategy.teardown_workspace.assert_called_once_with(
            workspace=ws1,
        )


# ---------------------------------------------------------------------------
# merge_group
# ---------------------------------------------------------------------------


class TestMergeGroup:
    """Tests for merge_group method."""

    @pytest.mark.unit
    async def test_merge_group_returns_group_result(self) -> None:
        """merge_group returns WorkspaceGroupResult."""
        ws1 = make_workspace(workspace_id="ws-1")
        ws2 = make_workspace(workspace_id="ws-2")

        mr1 = make_merge_result(workspace_id="ws-1")
        mr2 = make_merge_result(workspace_id="ws-2")

        mock_strategy = AsyncMock()
        mock_strategy.merge_workspace = AsyncMock(
            side_effect=[mr1, mr2],
        )
        mock_strategy.teardown_workspace = AsyncMock()

        service = _make_service(strategy=mock_strategy)
        result = await service.merge_group(workspaces=(ws1, ws2))

        assert isinstance(result, WorkspaceGroupResult)
        assert result.all_merged is True
        assert result.total_conflicts == 0
        assert len(result.merge_results) == 2
        assert result.duration_seconds >= 0.0

    @pytest.mark.unit
    async def test_merge_group_with_conflict(self) -> None:
        """merge_group reports conflicts in result."""
        ws = make_workspace(workspace_id="ws-1")
        conflict = MergeConflict(
            file_path="src/a.py",
            conflict_type=ConflictType.TEXTUAL,
        )
        mr = MergeResult(
            workspace_id="ws-1",
            branch_name="workspace/task-1",
            success=False,
            conflicts=(conflict,),
            duration_seconds=0.5,
        )

        mock_strategy = AsyncMock()
        mock_strategy.merge_workspace = AsyncMock(return_value=mr)
        mock_strategy.teardown_workspace = AsyncMock()

        service = _make_service(strategy=mock_strategy)
        result = await service.merge_group(workspaces=(ws,))

        assert result.all_merged is False
        assert result.total_conflicts == 1


# ---------------------------------------------------------------------------
# teardown_group
# ---------------------------------------------------------------------------


class TestTeardownGroup:
    """Tests for teardown_group method."""

    @pytest.mark.unit
    async def test_teardown_group_cleans_all(self) -> None:
        """teardown_group tears down all workspaces."""
        ws1 = make_workspace(workspace_id="ws-1")
        ws2 = make_workspace(workspace_id="ws-2")

        mock_strategy = AsyncMock()
        mock_strategy.teardown_workspace = AsyncMock()

        service = _make_service(strategy=mock_strategy)
        await service.teardown_group(workspaces=(ws1, ws2))

        assert mock_strategy.teardown_workspace.call_count == 2

    @pytest.mark.unit
    async def test_teardown_group_empty(self) -> None:
        """teardown_group with no workspaces does nothing."""
        mock_strategy = AsyncMock()
        mock_strategy.teardown_workspace = AsyncMock()

        service = _make_service(strategy=mock_strategy)
        await service.teardown_group(workspaces=())

        mock_strategy.teardown_workspace.assert_not_called()

    @pytest.mark.unit
    async def test_teardown_group_best_effort(self) -> None:
        """teardown_group continues on failure and raises combined."""
        ws1 = make_workspace(workspace_id="ws-1")
        ws2 = make_workspace(workspace_id="ws-2")

        mock_strategy = AsyncMock()
        mock_strategy.teardown_workspace = AsyncMock(
            side_effect=[
                WorkspaceCleanupError("ws-1 failed"),
                None,  # ws-2 succeeds
            ],
        )

        service = _make_service(strategy=mock_strategy)

        with pytest.raises(WorkspaceCleanupError, match="ws-1"):
            await service.teardown_group(workspaces=(ws1, ws2))

        # Both teardowns were attempted
        assert mock_strategy.teardown_workspace.call_count == 2
