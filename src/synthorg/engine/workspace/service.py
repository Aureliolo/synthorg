"""Workspace isolation service.

High-level service that coordinates workspace lifecycle:
setup, merge, and teardown for groups of agent workspaces.
"""

import time
from typing import TYPE_CHECKING
from uuid import uuid4

from synthorg.engine.errors import (
    WorkspaceCleanupError,
    WorkspaceLimitError,
    WorkspaceSetupError,
)
from synthorg.engine.workspace.merge import MergeOrchestrator
from synthorg.engine.workspace.models import (
    Workspace,
    WorkspaceGroupResult,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    WORKSPACE_GROUP_SETUP_COMPLETE,
    WORKSPACE_GROUP_SETUP_FAILED,
    WORKSPACE_GROUP_SETUP_START,
    WORKSPACE_GROUP_TEARDOWN_COMPLETE,
    WORKSPACE_GROUP_TEARDOWN_START,
    WORKSPACE_TEARDOWN_FAILED,
)

if TYPE_CHECKING:
    from synthorg.engine.workspace.config import (
        WorkspaceIsolationConfig,
    )
    from synthorg.engine.workspace.models import WorkspaceRequest
    from synthorg.engine.workspace.protocol import (
        WorkspaceIsolationStrategy,
    )

logger = get_logger(__name__)


class WorkspaceIsolationService:
    """Service for managing workspace isolation lifecycle.

    Coordinates creating, merging, and tearing down workspaces
    for groups of concurrent agent tasks.

    Args:
        strategy: Workspace isolation strategy implementation.
        config: Workspace isolation configuration.
    """

    __slots__ = ("_config", "_merge_orchestrator", "_strategy")

    def __init__(
        self,
        *,
        strategy: WorkspaceIsolationStrategy,
        config: WorkspaceIsolationConfig,
    ) -> None:
        self._strategy = strategy
        self._config = config
        pw = config.planner_worktrees
        self._merge_orchestrator = MergeOrchestrator(
            strategy=strategy,
            merge_order=pw.merge_order,
            conflict_escalation=pw.conflict_escalation,
            cleanup_on_merge=pw.cleanup_on_merge,
        )

    async def setup_group(
        self,
        *,
        requests: tuple[WorkspaceRequest, ...],
    ) -> tuple[Workspace, ...]:
        """Create workspaces for a group of agent tasks.

        Rolls back all already-created workspaces if any setup fails.

        Args:
            requests: Workspace creation requests.

        Returns:
            Tuple of created workspaces.

        Raises:
            WorkspaceLimitError: When max concurrent worktrees reached.
            WorkspaceSetupError: When git operations fail.
        """
        logger.info(
            WORKSPACE_GROUP_SETUP_START,
            count=len(requests),
        )

        workspaces: list[Workspace] = []
        try:
            for request in requests:
                ws = await self._strategy.setup_workspace(
                    request=request,
                )
                workspaces.append(ws)
        except (WorkspaceLimitError, WorkspaceSetupError) as exc:
            logger.warning(
                WORKSPACE_GROUP_SETUP_FAILED,
                count=len(requests),
                created=len(workspaces),
                error=str(exc),
            )
            await self._rollback_workspaces(workspaces)
            raise

        logger.info(
            WORKSPACE_GROUP_SETUP_COMPLETE,
            count=len(workspaces),
        )
        return tuple(workspaces)

    async def _rollback_workspaces(
        self,
        workspaces: list[Workspace],
    ) -> None:
        """Roll back already-created workspaces on setup failure.

        Best-effort: attempts all teardowns even if some fail.

        Args:
            workspaces: Workspaces to tear down during rollback.
        """
        for ws in workspaces:
            try:
                await self._strategy.teardown_workspace(
                    workspace=ws,
                )
            except Exception as exc:
                logger.warning(
                    WORKSPACE_TEARDOWN_FAILED,
                    workspace_id=ws.workspace_id,
                    error=f"Rollback cleanup failed: {exc}",
                )

    async def merge_group(
        self,
        *,
        workspaces: tuple[Workspace, ...],
    ) -> WorkspaceGroupResult:
        """Merge all workspaces and return aggregated result.

        Args:
            workspaces: Workspaces to merge.

        Returns:
            Aggregated merge result for the group.

        Raises:
            WorkspaceMergeError: When a merge operation fails fatally.
        """
        start = time.monotonic()
        merge_results = await self._merge_orchestrator.merge_all(
            workspaces=workspaces,
        )
        elapsed = time.monotonic() - start

        return WorkspaceGroupResult(
            group_id=str(uuid4()),
            merge_results=merge_results,
            duration_seconds=elapsed,
        )

    async def teardown_group(
        self,
        *,
        workspaces: tuple[Workspace, ...],
    ) -> None:
        """Tear down all workspaces in a group.

        Uses best-effort teardown: attempts all workspaces even if
        some fail, then raises a combined error.

        Args:
            workspaces: Workspaces to tear down.

        Raises:
            WorkspaceCleanupError: When any teardown operation fails.
        """
        logger.info(
            WORKSPACE_GROUP_TEARDOWN_START,
            count=len(workspaces),
        )

        errors: list[str] = []
        for workspace in workspaces:
            try:
                await self._strategy.teardown_workspace(
                    workspace=workspace,
                )
            except Exception as exc:
                errors.append(
                    f"workspace {workspace.workspace_id}: {exc}",
                )
                logger.warning(
                    WORKSPACE_TEARDOWN_FAILED,
                    workspace_id=workspace.workspace_id,
                    error=str(exc),
                )

        logger.info(
            WORKSPACE_GROUP_TEARDOWN_COMPLETE,
            count=len(workspaces),
            failures=len(errors),
        )

        if errors:
            msg = f"Failed to tear down {len(errors)} workspace(s): {'; '.join(errors)}"
            raise WorkspaceCleanupError(msg)
