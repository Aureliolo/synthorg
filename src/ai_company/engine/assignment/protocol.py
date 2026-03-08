"""Task assignment strategy protocol.

Defines the pluggable interface for assignment strategies.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ai_company.engine.assignment.models import (
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
        """
        ...
