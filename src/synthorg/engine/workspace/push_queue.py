"""Coordinator-owned serial merge/push queue.

Worktrees give local isolation; this queue gives forge-collision
safety.  N agents finishing concurrently on one project all route
their merge-to-default-branch + push-to-backend through ONE serial
FIFO per project, so concurrent pushes never collide at the git
backend.  Pluggable: it sits in front of the
:class:`~synthorg.engine.workspace.protocol.WorkspaceIsolationStrategy`
seam, so a future virtual-branch strategy supplies its own
``merge_workspace`` without changing this queue.
"""

import asyncio
from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649)
from typing import TYPE_CHECKING, NamedTuple

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.errors import (
    WorkspaceError,
    WorkspaceMergeError,
    WorkspacePushError,
)
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.workspace import (
    WORKSPACE_PUSH_QUEUE_ENQUEUED,
    WORKSPACE_PUSH_QUEUE_FAILED,
    WORKSPACE_PUSH_QUEUE_MERGED,
    WORKSPACE_PUSH_QUEUE_WORKER_FAILED,
)
from synthorg.observability.metrics_hub import record_push_queue_event

if TYPE_CHECKING:
    from synthorg.engine.workspace.git_backend import GitBackend
    from synthorg.engine.workspace.models import MergeResult, Workspace
    from synthorg.engine.workspace.protocol import WorkspaceIsolationStrategy

logger = get_logger(__name__)


class _QueuedMerge(NamedTuple):
    """One queued merge+push request and its result future."""

    workspace: Workspace
    future: asyncio.Future[MergeResult]


class PushQueueCoordinator:
    """Per-project serial merge+push processor.

    Args:
        project_id: Owning project (one coordinator per project).
        strategy: Workspace isolation strategy doing the actual merge.
        git_backend: Backend the merged default branch is pushed to.
        repo_root: Project working tree the push runs from.
        default_branch: Branch merged into and pushed.
        clock: Clock seam for duration measurement.
    """

    __slots__ = (
        "_clock",
        "_closing",
        "_default_branch",
        "_git_backend",
        "_project_id",
        "_queue",
        "_repo_root",
        "_strategy",
        "_worker",
    )

    def __init__(  # noqa: PLR0913 -- distinct collaborators; all required
        self,
        *,
        project_id: NotBlankStr,
        strategy: WorkspaceIsolationStrategy,
        git_backend: GitBackend,
        repo_root: Path,
        default_branch: NotBlankStr,
        clock: Clock | None = None,
    ) -> None:
        self._project_id = project_id
        self._strategy = strategy
        self._git_backend = git_backend
        self._repo_root = repo_root
        self._default_branch = default_branch
        self._clock: Clock = clock if clock is not None else SystemClock()
        # ``asyncio.Queue`` binds to the loop that constructs it; create
        # it in ``start()`` so the coordinator can restart on a fresh
        # event loop (pytest-asyncio per-test loops, lifecycle restart).
        self._queue: asyncio.Queue[_QueuedMerge | None] | None = None
        self._worker: asyncio.Task[None] | None = None
        # ``stop()`` sets this BEFORE enqueuing the sentinel so a late
        # ``enqueue_merge_push()`` racing the shutdown path refuses the
        # request instead of appending behind the sentinel (where it
        # would hang the caller forever).
        self._closing = False

    async def start(self) -> None:
        """Start the background queue worker (idempotent)."""
        if self._worker is None or self._worker.done():
            self._queue = asyncio.Queue()
            self._closing = False
            self._worker = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        """Drain in-flight work then stop the worker (idempotent)."""
        worker = self._worker
        if worker is None:
            return
        # Refuse any further enqueues BEFORE the sentinel goes in: a
        # request that wins the race after the sentinel is enqueued
        # would sit forever behind it.
        self._closing = True
        queue = self._queue
        if queue is not None:
            await queue.put(None)
        try:
            await worker
        finally:
            self._worker = None
            self._queue = None

    async def enqueue_merge_push(
        self,
        *,
        workspace: Workspace,
    ) -> MergeResult:
        """Enqueue a merge+push and await its turn.

        Returns:
            The :class:`MergeResult`.  A conflicted (``success=False``)
            merge is returned WITHOUT pushing.

        Raises:
            WorkspaceError: ``stop()`` has started; no new work is accepted.
            WorkspaceMergeError: The strategy merge failed fatally.
            WorkspacePushError: The backend push failed.
        """
        if self._closing:
            msg = "PushQueueCoordinator is stopping; new pushes refused"
            raise WorkspaceError(msg)
        queue = self._queue
        if queue is None:
            msg = "PushQueueCoordinator: enqueue called before start()"
            raise WorkspaceError(msg)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[MergeResult] = loop.create_future()
        await queue.put(_QueuedMerge(workspace=workspace, future=future))
        logger.info(
            WORKSPACE_PUSH_QUEUE_ENQUEUED,
            project_id=self._project_id,
            workspace_id=workspace.workspace_id,
        )
        record_push_queue_event(outcome="enqueued")
        return await future

    async def _worker_loop(self) -> None:
        """Process queued merges strictly one at a time (FIFO)."""
        queue = self._queue
        if queue is None:  # pragma: no cover - start() always assigns
            return
        try:
            # lint-allow: long-running-loop-kill-switch -- stop() puts a None sentinel
            while True:
                item = await queue.get()
                if item is None:
                    return
                try:
                    await self._process(item)
                except MemoryError, RecursionError:
                    raise
                except Exception as exc:
                    # A bug in _process must not kill the worker and strand
                    # every later caller; surface it to this caller and
                    # keep draining.
                    log_exception_redacted(
                        logger,
                        WORKSPACE_PUSH_QUEUE_WORKER_FAILED,
                        exc,
                        project_id=self._project_id,
                    )
                    if not item.future.done():
                        item.future.set_exception(exc)
        finally:
            # Worker is exiting (sentinel drain OR a re-raised
            # MemoryError/RecursionError). Fail every still-pending
            # item so their callers do not hang waiting on a future
            # the dead worker would never complete.
            while not queue.empty():
                pending = queue.get_nowait()
                if pending is None:
                    continue
                if not pending.future.done():
                    pending.future.set_exception(
                        WorkspaceError(
                            f"PushQueueCoordinator for project "
                            f"{self._project_id!r} stopped with pending work",
                        ),
                    )

    async def _push_default_branch(self, item: _QueuedMerge) -> bool:
        """Run the backend push; return ``True`` iff the push succeeded.

        Resolves ``item.future`` on every failure path (preserving any
        existing ``WorkspacePushError`` subtype). ``MemoryError`` and
        ``RecursionError`` propagate so the worker loop's outer
        ``finally`` can fail every remaining pending item.
        """
        try:
            await self._git_backend.push(
                project_id=self._project_id,
                repo_root=self._repo_root,
                branch=self._default_branch,
                base_branch=self._default_branch,
            )
        except MemoryError, RecursionError:
            raise
        except WorkspacePushError as exc:
            # Preserve forge-specific push error subtypes (rejection,
            # auth failure ...) so callers can discriminate; wrapping
            # in a fresh ``WorkspacePushError`` would erase the subclass.
            logger.warning(
                WORKSPACE_PUSH_QUEUE_FAILED,
                project_id=self._project_id,
                workspace_id=item.workspace.workspace_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            if not item.future.done():
                item.future.set_exception(exc)
            return False
        except Exception as exc:
            logger.warning(
                WORKSPACE_PUSH_QUEUE_FAILED,
                project_id=self._project_id,
                workspace_id=item.workspace.workspace_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            if not item.future.done():
                msg = (
                    f"Failed to push merged default branch for project "
                    f"{self._project_id!r}"
                )
                item.future.set_exception(WorkspacePushError(msg))
            return False
        return True

    async def _process(self, item: _QueuedMerge) -> None:
        """Merge then (on success) push; resolve the caller future."""
        try:
            merge_result = await self._strategy.merge_workspace(
                workspace=item.workspace,
            )
        except MemoryError, RecursionError:
            raise
        except WorkspaceMergeError as exc:
            if not item.future.done():
                item.future.set_exception(exc)
            return
        if not merge_result.success:
            # Conflicted merge: do not push a broken default branch.
            if not item.future.done():
                item.future.set_result(merge_result)
            return
        if not await self._push_default_branch(item):
            return
        logger.info(
            WORKSPACE_PUSH_QUEUE_MERGED,
            project_id=self._project_id,
            workspace_id=item.workspace.workspace_id,
            branch=item.workspace.branch_name,
        )
        record_push_queue_event(outcome="merged")
        if not item.future.done():
            item.future.set_result(merge_result)


__all__ = ["PushQueueCoordinator"]
