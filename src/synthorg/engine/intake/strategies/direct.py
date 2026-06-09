"""Direct intake strategy: unconditionally create a task."""

from synthorg.client.models import (
    ClientRequest,
    TaskRequirement,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.intake.models import IntakeResult
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.observability import get_logger
from synthorg.observability.events.review_pipeline import (
    INTAKE_DIRECT_TASK_CREATED,
)

logger = get_logger(__name__)


class DirectIntake:
    """Intake strategy that creates a task for every incoming request.

    No triage logic; every request is accepted as-is and a task is
    created via the injected :class:`TaskEngine`. Suitable for
    simulation backends that want to exercise the task lifecycle
    without an intake-agent round-trip.
    """

    def __init__(
        self,
        *,
        task_engine: TaskEngine,
        project: NotBlankStr = "simulation",
        requested_by: NotBlankStr = "intake-direct",
    ) -> None:
        """Initialize the direct intake strategy.

        Args:
            task_engine: Task engine used to create tasks.
            project: Project identifier stamped on created tasks.
            requested_by: Identity recorded as the task creator.
        """
        self._task_engine = task_engine
        self._project = project
        self._requested_by = requested_by

    async def process(self, request: ClientRequest) -> IntakeResult:
        """Create a task from the request and return an accepted result.

        Returns:
            An :class:`IntakeResult` with ``accepted=True`` carrying
            the new task's id; this strategy never rejects.
        """
        data = self._build_task_data(request.requirement)
        task = await self._task_engine.create_task(
            data,
            requested_by=self._requested_by,
        )
        logger.debug(
            INTAKE_DIRECT_TASK_CREATED,
            request_id=request.request_id,
            task_id=str(task.id),
        )
        return IntakeResult.accepted_result(
            request_id=request.request_id,
            task_id=str(task.id),
        )

    def _build_task_data(self, requirement: TaskRequirement) -> CreateTaskData:
        return CreateTaskData(
            title=requirement.title,
            description=requirement.description,
            type=requirement.task_type,
            priority=requirement.priority,
            project=self._project,
            created_by=self._requested_by,
            estimated_complexity=requirement.estimated_complexity,
        )
