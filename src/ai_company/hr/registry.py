"""Agent registry service.

Hot-pluggable agent registry for tracking active agents,
their identities, and lifecycle status transitions (D8.3).
"""

import asyncio
from typing import TYPE_CHECKING

from ai_company.core.enums import AgentStatus
from ai_company.hr.errors import (
    AgentAlreadyRegisteredError,
    AgentNotFoundError,
)
from ai_company.observability import get_logger
from ai_company.observability.events.hr import (
    HR_REGISTRY_AGENT_REGISTERED,
    HR_REGISTRY_AGENT_REMOVED,
    HR_REGISTRY_STATUS_UPDATED,
)

if TYPE_CHECKING:
    from ai_company.communication.bus_protocol import MessageBus
    from ai_company.core.agent import AgentIdentity

logger = get_logger(__name__)


class AgentRegistryService:
    """Hot-pluggable agent registry.

    Thread-safe via asyncio.Lock. Stores agent identities keyed
    by agent ID (string form of UUID).

    Args:
        message_bus: Optional message bus for HR notifications.
    """

    def __init__(
        self,
        *,
        message_bus: MessageBus | None = None,
    ) -> None:
        self._agents: dict[str, AgentIdentity] = {}
        self._lock = asyncio.Lock()
        self._message_bus = message_bus

    async def register(self, identity: AgentIdentity) -> None:
        """Register a new agent.

        Args:
            identity: The agent identity to register.

        Raises:
            AgentAlreadyRegisteredError: If the agent is already registered.
        """
        agent_key = str(identity.id)
        async with self._lock:
            if agent_key in self._agents:
                msg = f"Agent {identity.name!r} ({agent_key}) is already registered"
                logger.warning(
                    HR_REGISTRY_AGENT_REGISTERED,
                    agent_id=agent_key,
                    error=msg,
                )
                raise AgentAlreadyRegisteredError(msg)
            self._agents[agent_key] = identity

        logger.info(
            HR_REGISTRY_AGENT_REGISTERED,
            agent_id=agent_key,
            agent_name=str(identity.name),
            status=identity.status.value,
        )

    async def unregister(self, agent_id: str) -> AgentIdentity:
        """Remove an agent from the registry.

        Args:
            agent_id: The agent identifier to remove.

        Returns:
            The removed agent identity.

        Raises:
            AgentNotFoundError: If the agent is not found.
        """
        async with self._lock:
            identity = self._agents.pop(agent_id, None)
        if identity is None:
            msg = f"Agent {agent_id!r} not found in registry"
            logger.warning(HR_REGISTRY_AGENT_REMOVED, agent_id=agent_id, error=msg)
            raise AgentNotFoundError(msg)

        logger.info(
            HR_REGISTRY_AGENT_REMOVED,
            agent_id=agent_id,
            agent_name=str(identity.name),
        )
        return identity

    async def get(self, agent_id: str) -> AgentIdentity | None:
        """Retrieve an agent identity by ID.

        Args:
            agent_id: The agent identifier.

        Returns:
            The agent identity, or None if not found.
        """
        return self._agents.get(agent_id)

    async def get_by_name(self, name: str) -> AgentIdentity | None:
        """Retrieve an agent identity by name.

        Args:
            name: The agent name to search for.

        Returns:
            The first matching agent, or None.
        """
        name_lower = name.lower()
        for identity in self._agents.values():
            if str(identity.name).lower() == name_lower:
                return identity
        return None

    async def list_active(self) -> tuple[AgentIdentity, ...]:
        """List all agents with ACTIVE status.

        Returns:
            Tuple of active agent identities.
        """
        return tuple(a for a in self._agents.values() if a.status == AgentStatus.ACTIVE)

    async def list_by_department(
        self,
        department: str,
    ) -> tuple[AgentIdentity, ...]:
        """List agents in a specific department.

        Args:
            department: Department name to filter by.

        Returns:
            Tuple of matching agent identities.
        """
        dept_lower = department.lower()
        return tuple(
            a for a in self._agents.values() if str(a.department).lower() == dept_lower
        )

    async def update_status(
        self,
        agent_id: str,
        status: AgentStatus,
    ) -> AgentIdentity:
        """Update an agent's lifecycle status.

        Args:
            agent_id: The agent identifier.
            status: New status.

        Returns:
            Updated agent identity.

        Raises:
            AgentNotFoundError: If the agent is not found.
        """
        async with self._lock:
            identity = self._agents.get(agent_id)
            if identity is None:
                msg = f"Agent {agent_id!r} not found in registry"
                logger.warning(
                    HR_REGISTRY_STATUS_UPDATED,
                    agent_id=agent_id,
                    error=msg,
                )
                raise AgentNotFoundError(msg)
            updated = identity.model_copy(update={"status": status})
            self._agents[agent_id] = updated

        logger.info(
            HR_REGISTRY_STATUS_UPDATED,
            agent_id=agent_id,
            status=status.value,
        )
        return updated

    @property
    def agent_count(self) -> int:
        """Number of agents currently in the registry."""
        return len(self._agents)
