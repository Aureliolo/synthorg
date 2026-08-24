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
from synthorg.engine.routing_policy.capability_policy import CapabilityPolicy
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.task_assignment import (
    TASK_ASSIGNMENT_AGENT_SELECTED,
    TASK_ASSIGNMENT_BELOW_CAPABILITY_FLOOR,
    TASK_ASSIGNMENT_COMPLETE,
    TASK_ASSIGNMENT_FAILED,
    TASK_ASSIGNMENT_NO_ELIGIBLE,
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

    Validates task status and stamps the capability the work demands onto the
    request before delegating to the strategy. Does NOT mutate the task --
    callers are responsible for any subsequent status transitions.

    The requirement is derived here rather than by each caller so every
    assignment asks for the same rung: an agent is a fixed
    ``(role, model)`` unit, and the answer to work that needs
    more capability is a different agent, so which agents are eligible must
    not depend on which caller assembled the request.

    Args:
        strategy: The assignment strategy to delegate to.
        capability: The org's one capability policy. ``None`` leaves
            assignments ungated by capability.
    """

    __slots__ = ("_capability", "_strategy")

    def __init__(
        self,
        strategy: TaskAssignmentStrategy,
        *,
        capability: CapabilityPolicy | None = None,
    ) -> None:
        self._strategy = strategy
        self._capability = capability

    def assign(self, request: AssignmentRequest) -> AssignmentResult:
        """Assign a task to an agent using the configured strategy.

        Args:
            request: The assignment request. Its ``required_capability`` is
                overwritten from the task's own stakes and complexity when a
                policy is wired, so a caller cannot assign consequential work
                under a weaker requirement by omitting it.

        Returns:
            Assignment result from the strategy.

        Raises:
            TaskAssignmentError: If the task status is not eligible
                for assignment.
        """
        task = request.task

        if self._capability is not None:
            request = request.model_copy(
                update={
                    "required_capability": self._capability.required_for(
                        task.stakes, task.estimated_complexity
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

        # Stamping the requirement is not enforcing it. The strategy walks the
        # same ladder, but only when IT was also given a policy, so a service
        # holding one and delegating to a strategy without one would promise a
        # requirement and apply none. Refusing here covers the case the ladder
        # cannot: nobody the work's stakes permit at all.
        if self._capability is not None:
            unsanctioned = self._refuse_unsanctioned(request)
            if unsanctioned is not None:
                return unsanctioned

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

    def _refuse_unsanctioned(
        self,
        request: AssignmentRequest,
    ) -> AssignmentResult | None:
        """Refuse when the work's stakes permit none of the available agents.

        Only reachable above the configured park floor: below it a weaker
        agent is sanctioned and the ladder takes it. The organisation's answer
        here is an agent at the needed rung, not a stronger model behind an
        existing agent's name, so the reason names the rung to staff.

        Returns:
            The refusal, or ``None`` when at least one agent may take it.
        """
        assert self._capability is not None  # noqa: S101  # caller checks
        required = request.required_capability
        if required is None:
            return None
        if any(
            self._capability.judge(
                model=agent.model,
                stakes=request.stakes,
                complexity=request.task.estimated_complexity,
            ).sanctioned
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
