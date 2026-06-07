"""Manual task assignment strategy.

Assigns a task to its pre-designated agent identified by
``task.assigned_to``. The field may carry either the agent's UUID
(canonical, as populated by API callers) or the agent's declarative
``name`` (as populated by workflow AGENT_ASSIGNMENT nodes); both
forms are accepted.
"""

from typing import TYPE_CHECKING

from synthorg.core.enums import AgentStatus
from synthorg.engine.assignment._shared import STRATEGY_NAME_MANUAL
from synthorg.engine.assignment.models import (
    AssignmentCandidate,
    AssignmentRequest,
    AssignmentResult,
)
from synthorg.engine.errors import NoEligibleAgentError, TaskAssignmentError
from synthorg.observability import get_logger
from synthorg.observability.events.task_assignment import (
    TASK_ASSIGNMENT_FAILED,
    TASK_ASSIGNMENT_MANUAL_VALIDATED,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)


class ManualAssignmentStrategy:
    """Assigns a task to its pre-designated agent.

    Requires ``task.assigned_to`` to be set. Validates that
    the designated agent exists in the pool and is ACTIVE.
    """

    __slots__ = ()

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        return STRATEGY_NAME_MANUAL

    def _find_designated_agent(
        self,
        request: AssignmentRequest,
    ) -> AgentIdentity:
        """Find and validate the designated agent in the pool.

        ``task.assigned_to`` may carry either the agent's UUID
        (API path) or declarative ``name`` (workflow path); both
        forms are accepted.

        Returns:
            The :class:`AgentIdentity` whose UUID or name matches
            ``task.assigned_to`` and whose status is ACTIVE.

        Raises:
            TaskAssignmentError: If ``task.assigned_to`` is None.
            NoEligibleAgentError: If not in the pool or not ACTIVE.
        """
        task = request.task
        if task.assigned_to is None:
            msg = f"Manual assignment requires task.assigned_to (task {str(task.id)!r})"
            logger.warning(
                TASK_ASSIGNMENT_FAILED,
                task_id=str(task.id),
                strategy=self.name,
                error=msg,
            )
            raise TaskAssignmentError(msg)

        # UUID matching takes precedence over name matching: a single
        # combined predicate could return an earlier name match even when
        # a later agent has the exact UUID, silently selecting the wrong
        # agent on name/UUID collisions.
        designated = task.assigned_to
        agent = next(
            (a for a in request.available_agents if str(a.id) == designated),
            None,
        )
        if agent is None:
            agent = next(
                (a for a in request.available_agents if a.name == designated),
                None,
            )
        if agent is None:
            msg = (
                f"Designated agent {task.assigned_to!r} not found "
                f"(task {str(task.id)!r})"
            )
            logger.warning(
                TASK_ASSIGNMENT_FAILED,
                task_id=str(task.id),
                strategy=self.name,
                designated_agent=task.assigned_to,
                error=msg,
            )
            raise NoEligibleAgentError(msg)

        if agent.status != AgentStatus.ACTIVE:
            msg = (
                f"Designated agent {agent.name!r} is {agent.status.value!r}, "
                f"expected 'active' (task {str(task.id)!r})"
            )
            logger.warning(
                TASK_ASSIGNMENT_FAILED,
                task_id=str(task.id),
                strategy=self.name,
                agent_name=agent.name,
                agent_status=agent.status.value,
                error=msg,
            )
            raise NoEligibleAgentError(msg)

        return agent

    def assign(self, request: AssignmentRequest) -> AssignmentResult:
        """Assign to the agent specified by ``task.assigned_to``.

        Args:
            request: The assignment request.

        Returns:
            Assignment result with the designated agent.

        Raises:
            TaskAssignmentError: If ``task.assigned_to`` is None.
            NoEligibleAgentError: If the designated agent is not in
                the pool or is not ACTIVE.
        """
        agent = self._find_designated_agent(request)
        task = request.task

        candidate = AssignmentCandidate(
            agent_identity=agent,
            score=1.0,
            matched_skills=(),
            reason="Manually assigned",
        )

        logger.info(
            TASK_ASSIGNMENT_MANUAL_VALIDATED,
            task_id=str(task.id),
            agent_name=agent.name,
        )

        return AssignmentResult(
            task_id=str(task.id),
            strategy_used=self.name,
            selected=candidate,
            reason=f"Manually assigned to {agent.name!r}",
        )
