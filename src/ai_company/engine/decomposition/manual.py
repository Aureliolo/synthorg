"""Manual decomposition strategy.

Takes a pre-built ``DecompositionPlan`` at construction and returns it
from ``decompose()``, validating against context limits.
"""

from typing import TYPE_CHECKING

from ai_company.engine.errors import DecompositionDepthError, DecompositionError
from ai_company.observability import get_logger
from ai_company.observability.events.decomposition import (
    DECOMPOSITION_VALIDATION_ERROR,
)

if TYPE_CHECKING:
    from ai_company.core.task import Task
    from ai_company.engine.decomposition.models import (
        DecompositionContext,
        DecompositionPlan,
    )

logger = get_logger(__name__)


class ManualDecompositionStrategy:
    """Decomposition strategy using a pre-built plan.

    Validates the plan against decomposition context constraints
    (max subtasks, max depth) before returning it.
    """

    __slots__ = ("_plan",)

    def __init__(self, plan: DecompositionPlan) -> None:
        self._plan = plan

    async def decompose(
        self,
        task: Task,
        context: DecompositionContext,
    ) -> DecompositionPlan:
        """Return the pre-built plan after validation.

        Args:
            task: The parent task (used for ID validation).
            context: Decomposition constraints.

        Returns:
            The pre-built decomposition plan.

        Raises:
            DecompositionError: If the plan's parent_task_id doesn't
                match the task.
            DecompositionDepthError: If current depth exceeds max depth.
            DecompositionError: If subtask count exceeds max_subtasks.
        """
        if self._plan.parent_task_id != task.id:
            msg = (
                f"Plan parent_task_id {self._plan.parent_task_id!r} "
                f"does not match task id {task.id!r}"
            )
            logger.warning(DECOMPOSITION_VALIDATION_ERROR, error=msg)
            raise DecompositionError(msg)

        if context.current_depth >= context.max_depth:
            msg = (
                f"Decomposition depth {context.current_depth} "
                f"exceeds max depth {context.max_depth}"
            )
            logger.warning(DECOMPOSITION_VALIDATION_ERROR, error=msg)
            raise DecompositionDepthError(msg)

        if len(self._plan.subtasks) > context.max_subtasks:
            msg = (
                f"Plan has {len(self._plan.subtasks)} subtasks, "
                f"exceeding max of {context.max_subtasks}"
            )
            logger.warning(DECOMPOSITION_VALIDATION_ERROR, error=msg)
            raise DecompositionError(msg)

        return self._plan

    def get_strategy_name(self) -> str:
        """Return the strategy name."""
        return "manual"
