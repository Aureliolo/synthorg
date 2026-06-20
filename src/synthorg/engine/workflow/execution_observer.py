"""Bridge between TaskEngine events and workflow execution lifecycle.

Registered as a ``TaskEngine`` observer at application startup.
When a task transitions to a terminal status (COMPLETED, FAILED,
or CANCELLED), delegates to ``WorkflowExecutionService`` to update
the parent workflow execution accordingly.
"""

# ``TaskEngine``, ``TaskStateChanged``, and the two workflow
# repository protocols appear in public ``__init__`` / ``__call__``
# signatures. PEP 649 lazy annotation evaluation requires them in
# module globals so introspectors (``typing.get_type_hints`` /
# ``inspect.get_annotations``) can resolve the names at runtime.
from synthorg.engine.task_engine import (
    TaskEngine,
)
from synthorg.engine.task_engine_models import (
    TaskStateChanged,
)
from synthorg.engine.workflow.execution_service import (
    WorkflowExecutionService,
)
from synthorg.observability import get_logger
from synthorg.persistence.workflow_definition_protocol import (
    WorkflowDefinitionRepository,
)
from synthorg.persistence.workflow_execution_protocol import (
    WorkflowExecutionRepository,
)
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


class WorkflowExecutionObserver:
    """Bridges TaskEngine events to WorkflowExecutionService.

    Constructed once at application startup and registered via
    ``TaskEngine.register_observer()``.

    Args:
        definition_repo: Repository for reading workflow definitions.
        execution_repo: Repository for persisting execution state.
        task_engine: Required by the underlying ``WorkflowExecutionService``.
        config_resolver: Resolver the underlying service uses to resolve
            ``engine.max_subworkflow_depth`` per activation so operator
            overrides (DB > env > code default; YAML is a company-template
            ingestion format, not a precedence tier) flow through
            unchanged. ``None``
            falls back to the ``EngineBridgeConfig`` seed default; this
            observer never activates workflows (only forwards terminal
            task transitions), so the depth cap is immaterial here, but
            the resolver is threaded so a wired observer honours live
            settings if the underlying behaviour ever broadens.
    """

    def __init__(
        self,
        *,
        definition_repo: WorkflowDefinitionRepository,
        execution_repo: WorkflowExecutionRepository,
        task_engine: TaskEngine,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        self._service = WorkflowExecutionService(
            definition_repo=definition_repo,
            execution_repo=execution_repo,
            task_engine=task_engine,
            config_resolver=config_resolver,
        )

    async def __call__(self, event: TaskStateChanged) -> None:
        """Delegate a task state change to the execution service.

        Called by ``TaskEngine`` after every successful mutation.
        Forwards the event to ``WorkflowExecutionService.handle_task_state_changed``,
        which filters for terminal task transitions and updates execution state.
        """
        await self._service.handle_task_state_changed(event)
