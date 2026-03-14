"""Decomposition strategy protocol."""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.core.task import Task
    from synthorg.engine.decomposition.models import (
        DecompositionContext,
        DecompositionPlan,
    )


@runtime_checkable
class DecompositionStrategy(Protocol):
    """Protocol for task decomposition strategies.

    Implementations produce a ``DecompositionPlan`` from a parent task
    and a decomposition context. The plan describes subtask definitions
    and their dependency relationships.
    """

    async def decompose(
        self,
        task: Task,
        context: DecompositionContext,
    ) -> DecompositionPlan:
        """Decompose a task into subtasks.

        Args:
            task: The parent task to decompose.
            context: Decomposition constraints (max subtasks, depth).

        Returns:
            A decomposition plan with subtask definitions.
        """
        ...

    def get_strategy_name(self) -> str:
        """Return a human-readable name for this strategy."""
        ...
