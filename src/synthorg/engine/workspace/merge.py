"""Merge orchestrator for workspace branches.

Sequences workspace merges according to the configured merge order
and handles conflict escalation.
"""

import time
from typing import TYPE_CHECKING

from synthorg.core.enums import ConflictEscalation, MergeOrder
from synthorg.engine.errors import WorkspaceCleanupError, WorkspaceMergeError
from synthorg.engine.workspace.models import MergeResult
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    WORKSPACE_GROUP_MERGE_COMPLETE,
    WORKSPACE_GROUP_MERGE_START,
    WORKSPACE_MERGE_FAILED,
    WORKSPACE_SORT_WORKSPACES_APPENDED,
    WORKSPACE_TEARDOWN_FAILED,
)

if TYPE_CHECKING:
    from synthorg.engine.workspace.models import (
        Workspace,
    )
    from synthorg.engine.workspace.protocol import (
        WorkspaceIsolationStrategy,
    )

logger = get_logger(__name__)


class MergeOrchestrator:
    """Orchestrates sequential merging of workspace branches.

    Merges are always sequential (critical for git state consistency).
    The merge order and conflict escalation strategy are configurable.

    Args:
        strategy: Workspace isolation strategy for merge operations.
        merge_order: Order in which workspaces are merged.
        conflict_escalation: How to handle merge conflicts.
        cleanup_on_merge: Whether to teardown after successful merge.
    """

    __slots__ = (
        "_cleanup_on_merge",
        "_conflict_escalation",
        "_merge_order",
        "_strategy",
    )

    def __init__(
        self,
        *,
        strategy: WorkspaceIsolationStrategy,
        merge_order: MergeOrder,
        conflict_escalation: ConflictEscalation,
        cleanup_on_merge: bool = True,
    ) -> None:
        self._strategy = strategy
        self._merge_order = merge_order
        self._conflict_escalation = conflict_escalation
        self._cleanup_on_merge = cleanup_on_merge

    async def merge_all(
        self,
        *,
        workspaces: tuple[Workspace, ...],
        completion_order: tuple[str, ...] | None = None,
        priority_order: tuple[str, ...] | None = None,
    ) -> tuple[MergeResult, ...]:
        """Merge all workspaces sequentially in configured order.

        Note: Cleanup failures after successful merges are logged but
        do not propagate.

        Args:
            workspaces: Workspaces to merge.
            completion_order: Workspace IDs in completion order.
            priority_order: Workspace IDs in priority order.

        Returns:
            Tuple of merge results (may be partial on HUMAN stop).
        """
        ordered = self._sort_workspaces(
            workspaces=workspaces,
            completion_order=completion_order,
            priority_order=priority_order,
        )

        logger.info(
            WORKSPACE_GROUP_MERGE_START,
            count=len(ordered),
            merge_order=self._merge_order.value,
        )

        results: list[MergeResult] = []
        for workspace in ordered:
            ws_start = time.monotonic()
            try:
                result = await self._strategy.merge_workspace(
                    workspace=workspace,
                )
            except WorkspaceMergeError as exc:
                ws_elapsed = time.monotonic() - ws_start
                logger.warning(
                    WORKSPACE_MERGE_FAILED,
                    workspace_id=workspace.workspace_id,
                    error=str(exc),
                )
                result = MergeResult(
                    workspace_id=workspace.workspace_id,
                    branch_name=workspace.branch_name,
                    success=False,
                    duration_seconds=ws_elapsed,
                    escalation=self._conflict_escalation,
                )
                results.append(result)
                if self._conflict_escalation == ConflictEscalation.HUMAN:
                    break
                continue

            if not result.success:
                result = result.model_copy(
                    update={
                        "escalation": self._conflict_escalation,
                    },
                )
                results.append(result)

                if self._conflict_escalation == ConflictEscalation.HUMAN:
                    # Stop on conflict with HUMAN escalation
                    break
                # REVIEW_AGENT escalation: record conflict and continue merging
                continue

            results.append(result)

            if self._cleanup_on_merge:
                try:
                    await self._strategy.teardown_workspace(
                        workspace=workspace,
                    )
                except WorkspaceCleanupError as exc:
                    logger.warning(
                        WORKSPACE_TEARDOWN_FAILED,
                        workspace_id=workspace.workspace_id,
                        error=f"Post-merge cleanup failed: {exc}",
                    )

        logger.info(
            WORKSPACE_GROUP_MERGE_COMPLETE,
            total=len(results),
            successful=sum(1 for r in results if r.success),
        )
        return tuple(results)

    def _sort_workspaces(
        self,
        *,
        workspaces: tuple[Workspace, ...],
        completion_order: tuple[str, ...] | None,
        priority_order: tuple[str, ...] | None,
    ) -> tuple[Workspace, ...]:
        """Sort workspaces according to the configured merge order.

        Workspaces whose IDs are not in the ordering tuple are
        appended at the end to prevent silent data loss.

        Args:
            workspaces: Workspaces to sort.
            completion_order: Workspace IDs in completion order.
            priority_order: Workspace IDs in priority order.

        Returns:
            Sorted tuple of workspaces.
        """
        ws_map = {w.workspace_id: w for w in workspaces}

        if self._merge_order == MergeOrder.COMPLETION:
            if completion_order:
                return self._apply_ordering(ws_map, completion_order, workspaces)
            return workspaces

        if self._merge_order == MergeOrder.PRIORITY:
            if priority_order:
                return self._apply_ordering(ws_map, priority_order, workspaces)
            return workspaces

        # MANUAL order: return workspaces in their original input order
        return workspaces

    @staticmethod
    def _apply_ordering(
        ws_map: dict[str, Workspace],
        order: tuple[str, ...],
        workspaces: tuple[Workspace, ...],
    ) -> tuple[Workspace, ...]:
        """Apply an ordering tuple, appending unmentioned workspaces.

        Deduplicates the order tuple and appends workspaces not
        mentioned in the order in their original input order.

        Args:
            ws_map: Workspace ID to Workspace mapping.
            order: Ordered workspace IDs.
            workspaces: Original workspaces tuple for fallback ordering.

        Returns:
            Ordered workspaces with unmentioned ones appended.
        """
        seen: set[str] = set()
        unique_order: list[str] = []
        for wid in order:
            if wid not in seen:
                seen.add(wid)
                unique_order.append(wid)

        phantom = seen - set(ws_map.keys())
        if phantom:
            logger.warning(
                WORKSPACE_SORT_WORKSPACES_APPENDED,
                phantom_workspace_ids=sorted(phantom),
            )

        ordered_ids = set(unique_order)
        missing = set(ws_map.keys()) - ordered_ids
        if missing:
            logger.info(
                WORKSPACE_SORT_WORKSPACES_APPENDED,
                missing_workspace_ids=sorted(missing),
            )
        result = [ws_map[wid] for wid in unique_order if wid in ws_map]
        # Append missing workspaces in original input order
        result.extend(w for w in workspaces if w.workspace_id in missing)
        return tuple(result)
