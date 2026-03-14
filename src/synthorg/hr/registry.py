"""Agent registry service.

Hot-pluggable agent registry for tracking active agents,
their identities, and lifecycle status transitions (D8.3).
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.core.enums import AgentStatus
from synthorg.hr.errors import (
    AgentAlreadyRegisteredError,
    AgentNotFoundError,
)
from synthorg.observability import get_logger
from synthorg.observability.events.hr import (
    HR_REGISTRY_AGENT_REGISTERED,
    HR_REGISTRY_AGENT_REMOVED,
    HR_REGISTRY_IDENTITY_UPDATED,
    HR_REGISTRY_STATUS_UPDATED,
)

if TYPE_CHECKING:
    from typing import Any

    from synthorg.core.agent import AgentIdentity
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)


class AgentRegistryService:
    """Hot-pluggable agent registry.

    Coroutine-safe via asyncio.Lock within a single event loop.
    Stores agent identities keyed by agent ID (string form of UUID).
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentIdentity] = {}
        self._lock = asyncio.Lock()

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

    async def unregister(self, agent_id: NotBlankStr) -> AgentIdentity:
        """Remove an agent from the registry.

        Args:
            agent_id: The agent identifier to remove.

        Returns:
            The removed agent identity.

        Raises:
            AgentNotFoundError: If the agent is not found.
        """
        async with self._lock:
            identity = self._agents.pop(str(agent_id), None)
        if identity is None:
            msg = f"Agent {agent_id!r} not found in registry"
            logger.warning(
                HR_REGISTRY_AGENT_REMOVED,
                agent_id=str(agent_id),
                error=msg,
            )
            raise AgentNotFoundError(msg)

        logger.info(
            HR_REGISTRY_AGENT_REMOVED,
            agent_id=str(agent_id),
            agent_name=str(identity.name),
        )
        return identity

    async def get(self, agent_id: NotBlankStr) -> AgentIdentity | None:
        """Retrieve an agent identity by ID.

        Args:
            agent_id: The agent identifier.

        Returns:
            The agent identity, or None if not found.
        """
        async with self._lock:
            return self._agents.get(str(agent_id))

    async def get_by_name(self, name: NotBlankStr) -> AgentIdentity | None:
        """Retrieve an agent identity by name.

        Args:
            name: The agent name to search for.

        Returns:
            The first matching agent, or None.
        """
        async with self._lock:
            name_lower = str(name).lower()
            for identity in self._agents.values():
                if str(identity.name).lower() == name_lower:
                    return identity
            return None

    async def list_active(self) -> tuple[AgentIdentity, ...]:
        """List all agents with ACTIVE status.

        Returns:
            Tuple of active agent identities.
        """
        async with self._lock:
            return tuple(
                a for a in self._agents.values() if a.status == AgentStatus.ACTIVE
            )

    async def list_by_department(
        self,
        department: NotBlankStr,
    ) -> tuple[AgentIdentity, ...]:
        """List agents in a specific department.

        Args:
            department: Department name to filter by.

        Returns:
            Tuple of matching agent identities.
        """
        async with self._lock:
            dept_lower = str(department).lower()
            return tuple(
                a
                for a in self._agents.values()
                if str(a.department).lower() == dept_lower
            )

    async def update_status(
        self,
        agent_id: NotBlankStr,
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
        key = str(agent_id)
        async with self._lock:
            identity = self._agents.get(key)
            if identity is None:
                msg = f"Agent {agent_id!r} not found in registry"
                logger.warning(
                    HR_REGISTRY_STATUS_UPDATED,
                    agent_id=key,
                    error=msg,
                )
                raise AgentNotFoundError(msg)
            updated = identity.model_copy(update={"status": status})
            self._agents[key] = updated

        logger.info(
            HR_REGISTRY_STATUS_UPDATED,
            agent_id=key,
            status=status.value,
        )
        return updated

    # Allowlist of fields that may be updated via update_identity.
    # This prevents mass assignment of security-sensitive fields
    # (e.g. authority, status, tools.access_level) through the
    # generic update path.
    _UPDATABLE_FIELDS: frozenset[str] = frozenset({"level", "model"})

    async def update_identity(
        self,
        agent_id: NotBlankStr,
        **updates: Any,
    ) -> AgentIdentity:
        """Update agent identity fields via model_copy(update=...).

        Only fields in ``_UPDATABLE_FIELDS`` are accepted.  Use
        ``update_status`` for status changes.

        Args:
            agent_id: The agent identifier.
            **updates: Fields to update on the AgentIdentity.

        Returns:
            Updated agent identity.

        Raises:
            AgentNotFoundError: If the agent is not found.
            ValueError: If any field is not in the allowlist.
        """
        disallowed = set(updates.keys()) - self._UPDATABLE_FIELDS
        if disallowed:
            msg = (
                f"Fields not allowed for update_identity: "
                f"{sorted(disallowed)}; allowed: {sorted(self._UPDATABLE_FIELDS)}"
            )
            logger.warning(
                HR_REGISTRY_IDENTITY_UPDATED,
                agent_id=str(agent_id),
                error=msg,
            )
            raise ValueError(msg)

        key = str(agent_id)
        async with self._lock:
            identity = self._agents.get(key)
            if identity is None:
                msg = f"Agent {agent_id!r} not found in registry"
                logger.warning(
                    HR_REGISTRY_IDENTITY_UPDATED,
                    agent_id=key,
                    error=msg,
                )
                raise AgentNotFoundError(msg)
            updated = identity.model_copy(update=updates)
            self._agents[key] = updated

        logger.info(
            HR_REGISTRY_IDENTITY_UPDATED,
            agent_id=key,
            updated_fields=sorted(updates.keys()),
        )
        return updated

    async def agent_count(self) -> int:
        """Number of agents currently in the registry."""
        async with self._lock:
            return len(self._agents)
