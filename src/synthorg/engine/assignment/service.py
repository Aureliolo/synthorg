"""Task assignment service.

Orchestrates task assignment by delegating to a pluggable
``TaskAssignmentStrategy`` with logging and validation.
"""

from synthorg.core.task_enums import TaskStatus
from synthorg.engine.assignment.models import (
    AssignmentRequest,
    AssignmentResult,
)
from synthorg.engine.assignment.protocol import TaskAssignmentStrategy
from synthorg.engine.errors import TaskAssignmentError
from synthorg.engine.routing_policy.capability_floor import CapabilityFloorPolicy
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.task_assignment import (
    TASK_ASSIGNMENT_AGENT_SELECTED,
    TASK_ASSIGNMENT_BELOW_CAPABILITY_FLOOR,
    TASK_ASSIGNMENT_COMPLETE,
    TASK_ASSIGNMENT_FAILED,
    TASK_ASSIGNMENT_NO_ELIGIBLE,
    TASK_ASSIGNMENT_PROJECT_FILTERED,
    TASK_ASSIGNMENT_PROJECT_NO_ELIGIBLE,
    TASK_ASSIGNMENT_STARTED,
)

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

    Validates task status and stamps the task's stakes capability floor onto
    the request before delegating to the strategy. Does NOT mutate the task
    -- callers are responsible for any subsequent status transitions.

    The floor is derived here rather than by each caller so every assignment
    asks for the same rung: an agent is a fixed ``(role, personality, model)``
    unit, and the answer to work that needs more capability is a different
    agent, so which agents are eligible must not depend on which caller
    assembled the request.

    Args:
        strategy: The assignment strategy to delegate to.
        capability_floor: Stakes-to-rung floor plus the agent-rung reader.
            ``None`` leaves assignments ungated by capability.
    """

    __slots__ = ("_capability_floor", "_strategy")

    def __init__(
        self,
        strategy: TaskAssignmentStrategy,
        *,
        capability_floor: CapabilityFloorPolicy | None = None,
    ) -> None:
        self._strategy = strategy
        self._capability_floor = capability_floor

    def assign(self, request: AssignmentRequest) -> AssignmentResult:
        """Assign a task to an agent using the configured strategy.

        Args:
            request: The assignment request. Its ``required_capability`` is
                overwritten from the task's own stakes when a floor policy is
                wired, so a caller cannot assign consequential work under a
                weaker requirement than the org's floor by omitting it.

        Returns:
            Assignment result from the strategy.

        Raises:
            TaskAssignmentError: If the task status is not eligible
                for assignment.
        """
        task = request.task

        if self._capability_floor is not None:
            request = request.model_copy(
                update={
                    "required_capability": self._capability_floor.required_for(
                        task.stakes
                    ),
                },
            )

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

        # Stamping the requirement is not enforcing it. The strategy applies
        # the same rule, but only when IT was also given a policy, so a
        # service holding one and delegating to a strategy without one
        # promised a floor and applied none.
        if self._capability_floor is not None:
            below_floor = self._refuse_below_floor(request)
            if below_floor is not None:
                return below_floor
            request = request.model_copy(
                update={
                    "available_agents": tuple(
                        agent
                        for agent in request.available_agents
                        if self._capability_floor.clears(
                            agent.model, request.required_capability
                        )
                    ),
                },
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

    def _refuse_below_floor(
        self,
        request: AssignmentRequest,
    ) -> AssignmentResult | None:
        """Refuse the assignment when no agent runs at the required rung.

        The organisation's answer to this is an agent at the needed rung, not
        a stronger model behind an existing agent's name, so the reason names
        the rung the operator has to staff.

        Returns:
            The refusal, or ``None`` when at least one agent clears.
        """
        assert self._capability_floor is not None  # noqa: S101  # caller checks
        required = request.required_capability
        if required is None:
            return None
        if any(
            self._capability_floor.clears(agent.model, required)
            for agent in request.available_agents
        ):
            return None
        logger.warning(
            TASK_ASSIGNMENT_BELOW_CAPABILITY_FLOOR,
            task_id=str(request.task.id),
            strategy=self._strategy.name,
            stakes=request.stakes.value,
            required_capability=required,
            agent_count=len(request.available_agents),
        )
        return AssignmentResult(
            task_id=str(request.task.id),
            strategy_used=self._strategy.name,
            reason=(
                f"No available agent runs a {required} model, which "
                f"{request.stakes.value}-stakes work requires"
            ),
        )
