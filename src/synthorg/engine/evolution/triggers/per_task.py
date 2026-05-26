"""Per-task evolution trigger.

Fires after every task execution. Use with care -- this is the
most expensive trigger mode as it runs the evolution pipeline
on every task completion.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.evolution import (
    EVOLUTION_TRIGGER_REQUESTED,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.engine.evolution.protocols import EvolutionContext

logger = get_logger(__name__)


class PerTaskTrigger:
    """Trigger that fires after every task execution.

    Optionally requires a minimum number of tasks since the last
    evolution to avoid back-to-back evolutions.

    Args:
        min_tasks_since_last: Minimum tasks between evolutions.
    """

    def __init__(self, *, min_tasks_since_last: int = 1) -> None:
        self._min_tasks = max(1, min_tasks_since_last)
        self._tasks_since_evolution: dict[str, int] = {}
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Trigger name."""
        return "per_task"

    async def should_trigger(
        self,
        *,
        agent_id: NotBlankStr,
        context: EvolutionContext,  # noqa: ARG002
    ) -> bool:
        """Always triggers if enough tasks have elapsed.

        Returns:
            ``True`` when the per-agent task count reaches
            ``min_tasks`` (and resets); ``False`` otherwise.
        """
        key = str(agent_id)
        async with self._lock:
            count = self._tasks_since_evolution.get(key, 0) + 1
            self._tasks_since_evolution[key] = count

            if count >= self._min_tasks:
                del self._tasks_since_evolution[key]
                logger.info(
                    EVOLUTION_TRIGGER_REQUESTED,
                    agent_id=key,
                    trigger="per_task",
                    tasks_elapsed=count,
                )
                return True
            return False
