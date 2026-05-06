"""Bridge between TaskEngine events and workflow execution lifecycle.

Registered as a ``TaskEngine`` observer at application startup.
When a task transitions to a terminal status (COMPLETED, FAILED,
or CANCELLED), delegates to ``WorkflowExecutionService`` to update
the parent workflow execution accordingly.
"""

from typing import TYPE_CHECKING

from synthorg.engine.workflow.execution_service import (
    WorkflowExecutionService,
)
from synthorg.observability import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.engine.task_engine_models import TaskStateChanged
    from synthorg.persistence.workflow_definition_protocol import (
        WorkflowDefinitionRepository,
    )
    from synthorg.persistence.workflow_execution_protocol import (
        WorkflowExecutionRepository,
    )


class WorkflowExecutionObserver:
    """Bridges TaskEngine events to WorkflowExecutionService.

    Constructed once at application startup and registered via
    ``TaskEngine.register_observer()``.

    Args:
        definition_repo: Repository for reading workflow definitions.
        execution_repo: Repository for persisting execution state.
        task_engine: Required by the underlying ``WorkflowExecutionService``.
    """

    def __init__(
        self,
        *,
        definition_repo: WorkflowDefinitionRepository,
        execution_repo: WorkflowExecutionRepository,
        task_engine: TaskEngine,
        max_subworkflow_depth: int,
    ) -> None:
        self._service = WorkflowExecutionService(
            definition_repo=definition_repo,
            execution_repo=execution_repo,
            task_engine=task_engine,
            max_subworkflow_depth=max_subworkflow_depth,
        )

    async def __call__(self, event: TaskStateChanged) -> None:
        """Delegate a task state change to the execution service.

        Called by ``TaskEngine`` after every successful mutation.
        Forwards the event to ``WorkflowExecutionService.handle_task_state_changed``,
        which filters for terminal task transitions and updates execution state.
        """
        await self._service.handle_task_state_changed(event)
