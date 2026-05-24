"""Scoped context loaders for the classification pipeline.

Provides ``SameTaskLoader`` (wraps a single execution result) and
``TaskTreeLoader`` (queries the task tree via the task repository
to enrich the detection context with delegation chain data).
"""

from typing import TYPE_CHECKING, Final

from synthorg.budget.coordination_config import DetectionScope
from synthorg.communication.delegation.models import DelegationRequest
from synthorg.engine.classification.protocol import DetectionContext
from synthorg.engine.sanitization import sanitize_message
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.classification import (
    CONTEXT_LOADER_ERROR,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import paginate
from synthorg.persistence.task_protocol import TaskFilterSpec

if TYPE_CHECKING:
    from synthorg.core.task import Task
    from synthorg.core.types import NotBlankStr
    from synthorg.engine.loop_protocol import ExecutionResult
    from synthorg.persistence.task_protocol import TaskRepository

logger = get_logger(__name__)

# Internal constants by design: defensive caps on classification-tree
# recursion depth and on the length of cross-agent evidence included
# in findings; not exposed to the settings registry.
_MAX_TREE_DEPTH: Final[int] = 5
_SANITIZE_MAX_LENGTH: Final[int] = 2000


class SameTaskLoader:
    """Context loader for SAME_TASK scope.

    Wraps the single execution result into a ``DetectionContext``
    with no delegate or review data.
    """

    async def load(
        self,
        execution_result: ExecutionResult,
        agent_id: NotBlankStr,
        task_id: NotBlankStr,
    ) -> DetectionContext:
        """Build a SAME_TASK detection context.

        Args:
            execution_result: Primary execution result.
            agent_id: Agent identifier.
            task_id: Task identifier.

        Returns:
            Detection context with scope SAME_TASK.
        """
        return DetectionContext(
            execution_result=execution_result,
            agent_id=agent_id,
            task_id=task_id,
            scope=DetectionScope.SAME_TASK,
        )


class TaskTreeLoader:
    """Context loader for TASK_TREE scope.

    Queries the task repository for child tasks created via
    delegation (``parent_task_id`` linkage) and builds delegation
    request records from the task metadata.

    Cross-agent text data is sanitized via ``sanitize_message``
    before inclusion in the detection context.

    Args:
        task_repo: Task repository for querying child tasks.
    """

    def __init__(self, task_repo: TaskRepository) -> None:
        self._task_repo = task_repo

    async def load(
        self,
        execution_result: ExecutionResult,
        agent_id: NotBlankStr,
        task_id: NotBlankStr,
    ) -> DetectionContext:
        """Build a TASK_TREE detection context.

        Queries child tasks up to ``_MAX_TREE_DEPTH`` levels.
        Missing tasks are skipped with a warning log.  The loader
        never raises -- failures produce a degraded context.

        Args:
            execution_result: Primary execution result.
            agent_id: Agent identifier.
            task_id: Task identifier.

        Returns:
            Detection context with scope TASK_TREE.
        """
        delegation_requests = await self._collect_delegations(
            task_id,
            agent_id,
        )

        return DetectionContext(
            execution_result=execution_result,
            agent_id=agent_id,
            task_id=task_id,
            scope=DetectionScope.TASK_TREE,
            delegation_requests=delegation_requests,
        )

    async def _collect_delegations(
        self,
        root_task_id: NotBlankStr,
        agent_id: NotBlankStr,
    ) -> tuple[DelegationRequest, ...]:
        """Collect delegation requests from the task tree.

        Best-effort: catches all exceptions except MemoryError
        and RecursionError, logs them, and returns what was
        successfully loaded.

        Args:
            root_task_id: Root task to start traversal from.
            agent_id: Agent identifier for logging.

        Returns:
            Delegation requests found in the task tree.
        """
        try:
            collected: list[Task] = []
            async for page in paginate(
                lambda limit, offset: self._task_repo.query(
                    TaskFilterSpec(), limit=limit, offset=offset
                ),
                page_size=DEFAULT_PAGE_SIZE,
            ):
                collected.extend(page)
            all_tasks: tuple[Task, ...] = tuple(collected)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            log_exception_redacted(
                logger,
                CONTEXT_LOADER_ERROR,
                exc,
                loader="task_tree_loader",
                agent_id=agent_id,
                task_id=root_task_id,
            )
            return ()

        return _build_delegation_requests(
            all_tasks,
            root_task_id,
            agent_id,
        )


def _build_delegation_requests(
    all_tasks: tuple[Task, ...],
    root_task_id: NotBlankStr,
    agent_id: NotBlankStr,
) -> tuple[DelegationRequest, ...]:
    """Walk the task tree and build delegation request records.

    Pre-indexes ``all_tasks`` by ``parent_task_id`` into a dict so
    the BFS that follows is O(N + M) -- N tasks total, M tasks
    actually reachable in the tree -- instead of the previous
    O(depth * parents * N) full-table rescan per node.  Bounded to
    ``_MAX_TREE_DEPTH`` levels, sanitizes descriptions before
    including them in the returned requests.
    """
    tasks_by_parent: dict[str, list[Task]] = {}
    for task in all_tasks:
        parent = task.parent_task_id
        if parent is None:
            continue
        tasks_by_parent.setdefault(parent, []).append(task)

    requests: list[DelegationRequest] = []
    visited: set[str] = set()
    queue: list[str] = [root_task_id]
    depth = 0

    while queue and depth < _MAX_TREE_DEPTH:
        next_queue: list[str] = []
        for parent_id in queue:
            if parent_id in visited:
                continue
            visited.add(parent_id)
            for task in tasks_by_parent.get(parent_id, ()):
                refinement = sanitize_message(
                    task.description,
                    max_length=_SANITIZE_MAX_LENGTH,
                )
                sanitized_task = task.model_copy(
                    update={"description": refinement},
                )
                chain = task.delegation_chain
                delegator = chain[-1] if chain else agent_id
                requests.append(
                    DelegationRequest(
                        delegator_id=delegator,
                        delegatee_id=task.assigned_to or "unassigned",
                        task=sanitized_task,
                        refinement=refinement,
                    ),
                )
                next_queue.append(task.id)
        queue = next_queue
        depth += 1

    if queue:
        logger.warning(
            CONTEXT_LOADER_ERROR,
            loader="task_tree_loader",
            agent_id=agent_id,
            task_id=root_task_id,
            reason=(
                f"Task tree traversal truncated at depth {_MAX_TREE_DEPTH} "
                f"with {len(queue)} unvisited nodes remaining"
            ),
        )

    return tuple(requests)
