"""User-curated source selector.

Passes through an explicit list of agent IDs provided by the
user, validating that each agent exists in the registry.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.training import (
    HR_TRAINING_AGENT_NOT_FOUND,
    HR_TRAINING_SELECTION_COMPLETE,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.hr.seniority import SeniorityLevel

logger = get_logger(__name__)


class UserCuratedList:
    """Pass-through selector for user-provided agent IDs.

    Validates that all provided agent IDs exist in the registry,
    filtering out any that are not found.

    Args:
        registry: Agent registry service.
        agent_ids: Explicit list of agent IDs.
    """

    def __init__(
        self,
        *,
        registry: AgentRegistryService,
        agent_ids: tuple[NotBlankStr, ...],
    ) -> None:
        self._registry = registry
        self._agent_ids = agent_ids

    @property
    def name(self) -> str:
        """Selector strategy name."""
        return "user_curated"

    async def select(
        self,
        *,
        new_agent_role: NotBlankStr,  # noqa: ARG002
        new_agent_level: SeniorityLevel,  # noqa: ARG002
        new_agent_department: NotBlankStr | None = None,  # noqa: ARG002
    ) -> tuple[NotBlankStr, ...]:
        """Return the user-provided agent IDs, filtering invalid ones.

        Args:
            new_agent_role: Role of the new hire (unused).
            new_agent_level: Seniority level (unused).
            new_agent_department: Department of the new hire (unused).

        Returns:
            Validated agent IDs.
        """
        if not self._agent_ids:
            return ()

        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(self._check_exists(agent_id))
                for agent_id in self._agent_ids
            ]

        seen: set[str] = set()
        valid: list[NotBlankStr] = []
        for task in tasks:
            agent_id, exists = task.result()
            str_id = str(agent_id)
            if not exists:
                logger.warning(
                    HR_TRAINING_AGENT_NOT_FOUND,
                    selector="user_curated",
                    agent_id=str_id,
                )
            elif str_id not in seen:
                seen.add(str_id)
                valid.append(agent_id)

        logger.info(
            HR_TRAINING_SELECTION_COMPLETE,
            selector="user_curated",
            requested=len(self._agent_ids),
            valid=len(valid),
        )
        return tuple(valid)

    async def _check_exists(
        self,
        agent_id: NotBlankStr,
    ) -> tuple[NotBlankStr, bool]:
        """Check whether an agent exists in the registry.

        Returns:
            Tuple ``(NotBlankStr, bool)``.
        """
        identity = await self._registry.get(agent_id)
        return agent_id, identity is not None
