"""Task assignment strategy protocol.

Defines the pluggable interface for assignment strategies.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.engine.assignment.models import (
        AssignmentRequest,
        AssignmentResult,
    )


@runtime_checkable
class TaskAssignmentStrategy(Protocol):
    """Protocol for task assignment strategies.

    Implementations must be synchronous (pure computation, no I/O)
    and return an ``AssignmentResult`` with the selected agent and
    ranked alternatives. ``TaskAssignmentService`` calls ``assign()``
    synchronously — async implementations will NOT work correctly.

    Error signaling contract:

    * **``ManualAssignmentStrategy``** raises ``NoEligibleAgentError``
      when the designated agent is not found or not ACTIVE, and
      ``TaskAssignmentError`` when ``task.assigned_to`` is ``None``.
    * **Scoring-based strategies** (``RoleBasedAssignmentStrategy``,
      ``LoadBalancedAssignmentStrategy``,
      ``CostOptimizedAssignmentStrategy``,
      ``HierarchicalAssignmentStrategy``,
      ``AuctionAssignmentStrategy``) return
      ``AssignmentResult(selected=None, ...)`` when no agent meets
      the minimum score threshold.

    ``TaskAssignmentService`` propagates both patterns: it re-raises
    ``TaskAssignmentError`` (including its subclass
    ``NoEligibleAgentError``) and logs a warning when
    ``result.selected`` is ``None``, returning the result to the
    caller for handling.
    """

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        ...

    def assign(
        self,
        request: AssignmentRequest,
    ) -> AssignmentResult:
        """Assign a task to an agent based on the strategy's algorithm.

        Args:
            request: The assignment request with task and agent pool.

        Returns:
            Assignment result with selected agent and alternatives.
            ``selected`` may be ``None`` when no eligible agent is
            found (scoring strategies) — callers must check this.

        Raises:
            TaskAssignmentError: When preconditions are violated
                (e.g. missing ``assigned_to`` for manual strategy).
            NoEligibleAgentError: When the designated agent cannot
                be found or is not ACTIVE (manual strategy only).
        """
        ...
