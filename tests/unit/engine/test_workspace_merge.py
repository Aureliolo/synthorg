"""Tests for MergeOrchestrator."""

from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import ConflictEscalation, ConflictType, MergeOrder
from synthorg.engine.errors import WorkspaceMergeError
from synthorg.engine.workspace.merge import MergeOrchestrator
from synthorg.engine.workspace.models import (
    MergeConflict,
)

from .conftest import make_merge_result, make_workspace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conflict(
    *,
    file_path: str = "src/a.py",
) -> MergeConflict:
    return MergeConflict(
        file_path=file_path,
        conflict_type=ConflictType.TEXTUAL,
    )


def _make_orchestrator(
    *,
    strategy: AsyncMock | None = None,
    merge_order: MergeOrder = MergeOrder.COMPLETION,
    conflict_escalation: ConflictEscalation = ConflictEscalation.HUMAN,
    cleanup_on_merge: bool = True,
) -> MergeOrchestrator:
    return MergeOrchestrator(
        strategy=strategy or AsyncMock(),
        merge_order=merge_order,
        conflict_escalation=conflict_escalation,
        cleanup_on_merge=cleanup_on_merge,
    )


# ---------------------------------------------------------------------------
# Completion-order merging
# ---------------------------------------------------------------------------


class TestCompletionOrderMerge:
    """Tests for completion-order merge orchestration."""

    @pytest.mark.unit
    async def test_merge_all_completion_order(self) -> None:
        """Workspaces merge in completion order."""
        ws1 = make_workspace(workspace_id="ws-1", task_id="task-1")
        ws2 = make_workspace(workspace_id="ws-2", task_id="task-2")

        mock_strategy = AsyncMock()
        mock_strategy.merge_workspace = AsyncMock(
            side_effect=[
                make_merge_result(workspace_id="ws-1"),
                make_merge_result(workspace_id="ws-2"),
            ],
        )
        mock_strategy.teardown_workspace = AsyncMock()

        orch = _make_orchestrator(strategy=mock_strategy)
        results = await orch.merge_all(
            workspaces=(ws1, ws2),
            completion_order=("ws-1", "ws-2"),
        )

        assert len(results) == 2
        assert results[0].workspace_id == "ws-1"
        assert results[1].workspace_id == "ws-2"
        assert all(r.success for r in results)

    @pytest.mark.unit
    async def test_cleanup_called_after_success(self) -> None:
        """Teardown is called after each successful merge."""
        ws = make_workspace(workspace_id="ws-1")
        mock_strategy = AsyncMock()
        mock_strategy.merge_workspace = AsyncMock(
            return_value=make_merge_result(workspace_id="ws-1"),
        )
        mock_strategy.teardown_workspace = AsyncMock()

        orch = _make_orchestrator(
            strategy=mock_strategy,
            cleanup_on_merge=True,
        )
        await orch.merge_all(
            workspaces=(ws,),
            completion_order=("ws-1",),
        )

        mock_strategy.teardown_workspace.assert_called_once_with(
            workspace=ws,
        )

    @pytest.mark.unit
    async def test_no_cleanup_when_disabled(self) -> None:
        """Teardown is not called when cleanup_on_merge is False."""
        ws = make_workspace(workspace_id="ws-1")
        mock_strategy = AsyncMock()
        mock_strategy.merge_workspace = AsyncMock(
            return_value=make_merge_result(workspace_id="ws-1"),
        )
        mock_strategy.teardown_workspace = AsyncMock()

        orch = _make_orchestrator(
            strategy=mock_strategy,
            cleanup_on_merge=False,
        )
        await orch.merge_all(
            workspaces=(ws,),
            completion_order=("ws-1",),
        )

        mock_strategy.teardown_workspace.assert_not_called()


# ---------------------------------------------------------------------------
# Priority-order merging
# ---------------------------------------------------------------------------


class TestPriorityOrderMerge:
    """Tests for priority-order merge orchestration."""

    @pytest.mark.unit
    async def test_merge_all_priority_order(self) -> None:
        """Workspaces merge in priority order."""
        ws1 = make_workspace(workspace_id="ws-1", task_id="task-1")
        ws2 = make_workspace(workspace_id="ws-2", task_id="task-2")

        mock_strategy = AsyncMock()
        mock_strategy.merge_workspace = AsyncMock(
            side_effect=[
                make_merge_result(workspace_id="ws-2"),
                make_merge_result(workspace_id="ws-1"),
            ],
        )
        mock_strategy.teardown_workspace = AsyncMock()

        orch = _make_orchestrator(
            strategy=mock_strategy,
            merge_order=MergeOrder.PRIORITY,
        )
        # Priority order: ws-2 before ws-1
        results = await orch.merge_all(
            workspaces=(ws1, ws2),
            priority_order=("ws-2", "ws-1"),
        )

        assert len(results) == 2
        assert results[0].workspace_id == "ws-2"
        assert results[1].workspace_id == "ws-1"


# ---------------------------------------------------------------------------
# Conflict escalation
# ---------------------------------------------------------------------------


class TestConflictEscalation:
    """Tests for conflict handling during merge."""

    @pytest.mark.unit
    async def test_human_escalation_stops_on_conflict(self) -> None:
        """HUMAN escalation stops merging on first conflict."""
        ws1 = make_workspace(workspace_id="ws-1", task_id="task-1")
        ws2 = make_workspace(workspace_id="ws-2", task_id="task-2")

        conflict = _make_conflict()
        mock_strategy = AsyncMock()
        mock_strategy.merge_workspace = AsyncMock(
            side_effect=[
                make_merge_result(
                    workspace_id="ws-1",
                    success=False,
                    conflicts=(conflict,),
                    merged_commit_sha=None,
                ),
                make_merge_result(workspace_id="ws-2"),
            ],
        )
        mock_strategy.teardown_workspace = AsyncMock()

        orch = _make_orchestrator(
            strategy=mock_strategy,
            conflict_escalation=ConflictEscalation.HUMAN,
        )
        results = await orch.merge_all(
            workspaces=(ws1, ws2),
            completion_order=("ws-1", "ws-2"),
        )

        # Should stop after first conflict
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].escalation is ConflictEscalation.HUMAN
        assert mock_strategy.merge_workspace.await_count == 1

    @pytest.mark.unit
    async def test_review_agent_continues_on_conflict(self) -> None:
        """REVIEW_AGENT escalation flags conflict and continues."""
        ws1 = make_workspace(workspace_id="ws-1", task_id="task-1")
        ws2 = make_workspace(workspace_id="ws-2", task_id="task-2")

        conflict = _make_conflict()
        mock_strategy = AsyncMock()
        mock_strategy.merge_workspace = AsyncMock(
            side_effect=[
                make_merge_result(
                    workspace_id="ws-1",
                    success=False,
                    conflicts=(conflict,),
                    merged_commit_sha=None,
                ),
                make_merge_result(workspace_id="ws-2"),
            ],
        )
        mock_strategy.teardown_workspace = AsyncMock()

        orch = _make_orchestrator(
            strategy=mock_strategy,
            conflict_escalation=ConflictEscalation.REVIEW_AGENT,
        )
        results = await orch.merge_all(
            workspaces=(ws1, ws2),
            completion_order=("ws-1", "ws-2"),
        )

        # Should continue past conflict
        assert len(results) == 2
        assert results[0].success is False
        assert results[0].escalation is ConflictEscalation.REVIEW_AGENT
        assert results[1].success is True


# ---------------------------------------------------------------------------
# Manual-order merging
# ---------------------------------------------------------------------------


class TestManualOrderMerge:
    """Tests for manual-order (as-given) merge."""

    @pytest.mark.unit
    async def test_merge_all_manual_order(self) -> None:
        """Manual order uses workspaces as given."""
        ws1 = make_workspace(workspace_id="ws-1")
        ws2 = make_workspace(workspace_id="ws-2")

        mock_strategy = AsyncMock()
        mock_strategy.merge_workspace = AsyncMock(
            side_effect=[
                make_merge_result(workspace_id="ws-1"),
                make_merge_result(workspace_id="ws-2"),
            ],
        )
        mock_strategy.teardown_workspace = AsyncMock()

        orch = _make_orchestrator(
            strategy=mock_strategy,
            merge_order=MergeOrder.MANUAL,
        )
        results = await orch.merge_all(workspaces=(ws1, ws2))

        assert len(results) == 2
        assert results[0].workspace_id == "ws-1"
        assert results[1].workspace_id == "ws-2"


# ---------------------------------------------------------------------------
# Merge error handling
# ---------------------------------------------------------------------------


class TestMergeErrorHandling:
    """Tests for error handling during merge_all."""

    @pytest.mark.unit
    async def test_merge_exception_creates_failure_result(self) -> None:
        """WorkspaceMergeError creates a failure MergeResult."""
        ws = make_workspace(workspace_id="ws-1")

        mock_strategy = AsyncMock()
        mock_strategy.merge_workspace = AsyncMock(
            side_effect=WorkspaceMergeError("checkout failed"),
        )
        mock_strategy.teardown_workspace = AsyncMock()

        orch = _make_orchestrator(
            strategy=mock_strategy,
            conflict_escalation=ConflictEscalation.REVIEW_AGENT,
        )
        results = await orch.merge_all(
            workspaces=(ws,),
            completion_order=("ws-1",),
        )

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].escalation is ConflictEscalation.REVIEW_AGENT

    @pytest.mark.unit
    async def test_merge_exception_human_stops(self) -> None:
        """WorkspaceMergeError with HUMAN escalation stops merge."""
        ws1 = make_workspace(workspace_id="ws-1")
        ws2 = make_workspace(workspace_id="ws-2")

        mock_strategy = AsyncMock()
        mock_strategy.merge_workspace = AsyncMock(
            side_effect=[
                WorkspaceMergeError("checkout failed"),
                make_merge_result(workspace_id="ws-2"),
            ],
        )
        mock_strategy.teardown_workspace = AsyncMock()

        orch = _make_orchestrator(
            strategy=mock_strategy,
            conflict_escalation=ConflictEscalation.HUMAN,
        )
        results = await orch.merge_all(
            workspaces=(ws1, ws2),
            completion_order=("ws-1", "ws-2"),
        )

        # Should stop after exception with HUMAN escalation
        assert len(results) == 1
        assert results[0].success is False
        assert mock_strategy.merge_workspace.await_count == 1


# ---------------------------------------------------------------------------
# Workspace sorting with missing IDs
# ---------------------------------------------------------------------------


class TestSortWorkspaces:
    """Tests for _sort_workspaces ordering and warning."""

    @pytest.mark.unit
    async def test_unmentioned_workspaces_appended(self) -> None:
        """Workspaces not in ordering tuple are appended."""
        ws1 = make_workspace(workspace_id="ws-1")
        ws2 = make_workspace(workspace_id="ws-2")
        ws3 = make_workspace(workspace_id="ws-3")

        mock_strategy = AsyncMock()
        mock_strategy.merge_workspace = AsyncMock(
            side_effect=[
                make_merge_result(workspace_id="ws-1"),
                make_merge_result(workspace_id="ws-3"),
                make_merge_result(workspace_id="ws-2"),
            ],
        )
        mock_strategy.teardown_workspace = AsyncMock()

        orch = _make_orchestrator(strategy=mock_strategy)
        # Only mention ws-1 in completion order — ws-2 and ws-3
        # should be appended
        results = await orch.merge_all(
            workspaces=(ws1, ws2, ws3),
            completion_order=("ws-1",),
        )

        assert len(results) == 3
        # ws-1 comes first (explicitly ordered)
        assert results[0].workspace_id == "ws-1"
