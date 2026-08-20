"""Decomposition strategy protocol."""

from typing import Protocol, runtime_checkable

from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import DecompositionPlan


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


@runtime_checkable
class WorkspaceInventory(Protocol):
    """Protocol answering what a project's workspace currently holds.

    Narrow on purpose. Decomposition needs one fact about the workspace and
    has no business reaching the provisioning service that owns it: a planner
    must never provision, re-provision or otherwise touch the tree it is being
    told about.
    """

    async def describe_inventory(self, project_id: NotBlankStr) -> str:
        """Describe the project's workspace contents.

        Args:
            project_id: The project being planned for.

        Returns:
            A phrase naming what the workspace holds, worded so an empty one
            reads as "there is nothing there" rather than "unknown".
        """
        ...
