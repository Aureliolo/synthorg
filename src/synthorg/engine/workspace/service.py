"""Workspace isolation service.

High-level service that coordinates workspace lifecycle:
setup, merge, and teardown for groups of agent workspaces.
"""

import asyncio
from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649)
from typing import TYPE_CHECKING
from uuid import uuid4

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    WorkspaceCleanupError,
)
from synthorg.engine.workspace.merge import MergeOrchestrator
from synthorg.engine.workspace.models import (
    Workspace,
    WorkspaceGroupResult,
)
from synthorg.engine.workspace.push_queue import PushQueueCoordinator
from synthorg.observability import get_logger, safe_error_description
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
    from synthorg.engine.workspace.git_backend import GitBackend
    from synthorg.engine.workspace.models import MergeResult, WorkspaceRequest
    from synthorg.engine.workspace.protocol import (
        WorkspaceIsolationStrategy,
    )

logger = get_logger(__name__)

_DEFAULT_BRANCH: NotBlankStr = NotBlankStr("main")


class WorkspaceIsolationService:
    """Service for managing workspace isolation lifecycle.

    Coordinates creating, merging, and tearing down workspaces
    for groups of concurrent agent tasks.

    Args:
        strategy: Workspace isolation strategy implementation.
        config: Workspace isolation configuration.
    """

    __slots__ = (
        "_clock",
        "_config",
        "_default_branch",
        "_git_backend",
        "_merge_orchestrator",
        "_push_queues",
        "_push_queues_lock",
        "_strategy",
    )

    def __init__(
        self,
        *,
        strategy: WorkspaceIsolationStrategy,
        config: WorkspaceIsolationConfig,
        git_backend: GitBackend | None = None,
        default_branch: NotBlankStr = _DEFAULT_BRANCH,
        clock: Clock | None = None,
    ) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._strategy = strategy
        self._config = config
        self._git_backend = git_backend
        self._default_branch = default_branch
        self._push_queues: dict[str, PushQueueCoordinator] = {}
        self._push_queues_lock = asyncio.Lock()
        pw = config.planner_worktrees
        self._merge_orchestrator = MergeOrchestrator(
            strategy=strategy,
            merge_order=pw.merge_order,
            conflict_escalation=pw.conflict_escalation,
            cleanup_on_merge=pw.cleanup_on_merge,
            clock=self._clock,
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
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            # Catch ``Exception`` so any setup failure -- not just the
            # documented ``WorkspaceLimitError`` / ``WorkspaceSetupError``
            # -- triggers rollback. Without this fallback an
            # unexpected error after partial setup would leak the
            # already-created workspaces.
            logger.warning(
                WORKSPACE_GROUP_SETUP_FAILED,
                count=len(requests),
                created=len(workspaces),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                # Rollback cleanup errors can wrap filesystem / DB
                # exceptions whose str() embeds paths or connection
                # strings.
                logger.warning(
                    WORKSPACE_TEARDOWN_FAILED,
                    workspace_id=ws.workspace_id,
                    reason="rollback_cleanup_failed",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
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
        start = self._clock.monotonic()
        merge_results = await self._merge_orchestrator.merge_all(
            workspaces=workspaces,
        )
        elapsed = self._clock.monotonic() - start

        return WorkspaceGroupResult(
            group_id=str(uuid4()),
            merge_results=merge_results,
            duration_seconds=elapsed,
        )

    async def _get_or_create_queue(
        self,
        *,
        project_id: NotBlankStr,
        repo_root: Path,
    ) -> PushQueueCoordinator:
        """Return the project's push queue, creating it once on first use.

        Double-checked under ``_push_queues_lock`` so two agents
        finishing concurrently on a fresh project create exactly one
        coordinator.
        """
        existing = self._push_queues.get(project_id)
        if existing is not None:
            return existing
        async with self._push_queues_lock:
            existing = self._push_queues.get(project_id)
            if existing is not None:
                return existing
            if self._git_backend is None:  # pragma: no cover - guarded by caller
                msg = "push queue requires a git backend"
                raise WorkspaceCleanupError(msg)
            queue = PushQueueCoordinator(
                project_id=project_id,
                strategy=self._strategy,
                git_backend=self._git_backend,
                repo_root=repo_root,
                default_branch=self._default_branch,
                clock=self._clock,
            )
            await queue.start()
            self._push_queues[project_id] = queue
            return queue

    async def merge_workspace_with_push(
        self,
        *,
        workspace: Workspace,
        project_id: NotBlankStr,
        repo_root: Path,
    ) -> MergeResult:
        """Merge *workspace* then push the default branch, serialised.

        When no git backend is wired the merge still runs (via the
        strategy) but nothing is pushed -- this keeps the call site
        uniform whether or not durable backing is configured.

        Args:
            workspace: The agent workspace to merge back.
            project_id: Owning project (selects the serial queue).
            repo_root: Project working tree the push runs from.

        Returns:
            The :class:`MergeResult`.

        Raises:
            WorkspaceMergeError: The merge failed fatally.
            WorkspacePushError: The backend push failed.
        """
        if self._git_backend is None:
            return await self._strategy.merge_workspace(workspace=workspace)
        queue = await self._get_or_create_queue(
            project_id=project_id,
            repo_root=repo_root,
        )
        return await queue.enqueue_merge_push(workspace=workspace)

    async def shutdown(self) -> None:
        """Stop every per-project push queue (best-effort, all attempted)."""
        async with self._push_queues_lock:
            queues = tuple(self._push_queues.values())
            self._push_queues.clear()
        for queue in queues:
            try:
                await queue.stop()
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    WORKSPACE_TEARDOWN_FAILED,
                    reason="push_queue_stop_failed",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
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
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                # The ``errors`` list flows into
                # ``WorkspaceCleanupError`` which callers may log as
                # a message; raw ``exc`` text could leak DB
                # credentials or container ids. Use the same
                # scrubbed string as the warning log below.
                errors.append(
                    f"workspace {workspace.workspace_id}: "
                    f"{safe_error_description(exc)}",
                )
                logger.warning(
                    WORKSPACE_TEARDOWN_FAILED,
                    workspace_id=workspace.workspace_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        logger.info(
            WORKSPACE_GROUP_TEARDOWN_COMPLETE,
            count=len(workspaces),
            failures=len(errors),
        )

        if errors:
            msg = f"Failed to tear down {len(errors)} workspace(s): {'; '.join(errors)}"
            raise WorkspaceCleanupError(msg)
