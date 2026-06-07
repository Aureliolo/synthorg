"""Task assignment service.

Orchestrates task assignment by delegating to a pluggable
``TaskAssignmentStrategy`` with logging and validation.
"""

from typing import TYPE_CHECKING

from synthorg.core.enums import TaskStatus
from synthorg.engine.errors import TaskAssignmentError
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.task_assignment import (
    TASK_ASSIGNMENT_AGENT_SELECTED,
    TASK_ASSIGNMENT_COMPLETE,
    TASK_ASSIGNMENT_FAILED,
    TASK_ASSIGNMENT_NO_ELIGIBLE,
    TASK_ASSIGNMENT_PROJECT_FILTERED,
    TASK_ASSIGNMENT_PROJECT_NO_ELIGIBLE,
    TASK_ASSIGNMENT_STARTED,
)

if TYPE_CHECKING:
    from synthorg.engine.assignment.models import (
        AssignmentRequest,
        AssignmentResult,
    )
    from synthorg.engine.assignment.protocol import TaskAssignmentStrategy

logger = get_logger(__name__)

# Tasks in CREATED, FAILED, INTERRUPTED, or SUSPENDED can be assigned
# directly.  BLOCKED tasks must first be unblocked (transition to
# ASSIGNED via the task lifecycle), so they are not directly assignable.
_ASSIGNABLE_STATUSES = frozenset(
    {
        TaskStatus.CREATED,
        TaskStatus.FAILED,
        TaskStatus.INTERRUPTED,
        TaskStatus.SUSPENDED,
    },
)


class TaskAssignmentService:
    """Orchestrates task assignment via a pluggable strategy.

    Validates task status before delegating to the strategy.
    Does NOT mutate the task -- callers are responsible for any
    subsequent status transitions.
    """

    __slots__ = ("_strategy",)

    def __init__(self, strategy: TaskAssignmentStrategy) -> None:
        self._strategy = strategy

    def assign(self, request: AssignmentRequest) -> AssignmentResult:
        """Assign a task to an agent using the configured strategy.

        Args:
            request: The assignment request.

        Returns:
            Assignment result from the strategy.

        Raises:
            TaskAssignmentError: If the task status is not eligible
                for assignment.
        """
        task = request.task

        if task.status not in _ASSIGNABLE_STATUSES:
            msg = (
                f"Task {str(task.id)!r} has status {task.status.value!r}, "
                f"expected one of "
                f"{sorted(s.value for s in _ASSIGNABLE_STATUSES)}"
            )
            logger.warning(
                TASK_ASSIGNMENT_FAILED,
                task_id=str(task.id),
                status=task.status.value,
                error=msg,
            )
            raise TaskAssignmentError(msg)

        # Filter to project team members when specified.
        if request.project_team:
            from synthorg.engine.assignment.models import (  # noqa: PLC0415
                AssignmentResult,
            )

            team_set = frozenset(request.project_team)
            filtered = tuple(
                a for a in request.available_agents if str(a.id) in team_set
            )
            if not filtered:
                logger.warning(
                    TASK_ASSIGNMENT_PROJECT_NO_ELIGIBLE,
                    task_id=str(task.id),
                    available_agents=len(request.available_agents),
                    project_team_size=len(request.project_team),
                )
                return AssignmentResult(
                    task_id=str(task.id),
                    strategy_used=self._strategy.name,
                    reason=("No available agents are members of the project team"),
                )
            logger.info(
                TASK_ASSIGNMENT_PROJECT_FILTERED,
                task_id=str(task.id),
                total_agents=len(request.available_agents),
                eligible_agents=len(filtered),
            )
            request = request.model_copy(
                update={"available_agents": filtered},
            )

        logger.info(
            TASK_ASSIGNMENT_STARTED,
            task_id=str(task.id),
            strategy=self._strategy.name,
            agent_count=len(request.available_agents),
        )

        try:
            result = self._strategy.assign(request)
        except TaskAssignmentError:
            raise  # already logged by the strategy
        except Exception as exc:
            log_exception_redacted(
                logger,
                TASK_ASSIGNMENT_FAILED,
                exc,
                task_id=str(task.id),
                strategy=self._strategy.name,
            )
            raise

        if result.selected is not None:
            logger.info(
                TASK_ASSIGNMENT_AGENT_SELECTED,
                task_id=str(task.id),
                agent_name=result.selected.agent_identity.name,
                score=result.selected.score,
                strategy=result.strategy_used,
            )
        else:
            logger.warning(
                TASK_ASSIGNMENT_NO_ELIGIBLE,
                task_id=str(task.id),
                strategy=self._strategy.name,
                reason=result.reason,
            )

        logger.info(
            TASK_ASSIGNMENT_COMPLETE,
            task_id=str(task.id),
            strategy=result.strategy_used,
            selected=result.selected is not None,
            alternatives=len(result.alternatives),
        )

        return result
