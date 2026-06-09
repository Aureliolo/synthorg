"""Protocol for identity version stores.

Defines the interface for versioned identity storage with
rollback support, used by the evolution system to manage
agent identity mutations and reversions.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.agent import AgentIdentity
from synthorg.core.types import NotBlankStr
from synthorg.versioning.models import VersionSnapshot


@runtime_checkable
class IdentityVersionStore(Protocol):
    """Versioned identity storage with rollback support.

    Wraps ``AgentRegistryService`` and ``VersioningService`` to
    provide a unified interface for storing, retrieving, and
    rolling back agent identity versions.

    Implementations include append-only (strong audit trail,
    safe default) and copy-on-write (separate version pointer).
    """

    async def put(
        self,
        agent_id: NotBlankStr,
        identity: AgentIdentity,
        *,
        saved_by: NotBlankStr,
    ) -> VersionSnapshot[AgentIdentity]:
        """Store a new identity version.

        Updates the agent's current identity in the registry
        and persists a versioned snapshot.

        Args:
            agent_id: Agent to update.
            identity: New identity state.
            saved_by: Actor triggering the change.

        Returns:
            The created version snapshot.

        Raises:
            AgentNotFoundError: If agent does not exist.
            PersistenceError: If versioning fails.
        """
        ...

    async def get_current(
        self,
        agent_id: NotBlankStr,
    ) -> AgentIdentity | None:
        """Get the current active identity for an agent.

        Args:
            agent_id: Agent to look up.

        Returns:
            Current identity, or None if agent not found.
        """
        ...

    async def get_version(
        self,
        agent_id: NotBlankStr,
        version: int,
    ) -> AgentIdentity | None:
        """Get a specific identity version.

        Args:
            agent_id: Agent to look up.
            version: Version number to retrieve.

        Returns:
            Identity at that version, or None if not found.
        """
        ...

    async def list_versions(
        self,
        agent_id: NotBlankStr,
    ) -> tuple[VersionSnapshot[AgentIdentity], ...]:
        """List all identity versions for an agent.

        Args:
            agent_id: Agent to look up.

        Returns:
            Tuple of version snapshots, newest first.
        """
        ...

    async def set_current(
        self,
        agent_id: NotBlankStr,
        version: int,
    ) -> AgentIdentity:
        """Set the current identity to a specific version (rollback).

        Args:
            agent_id: Agent to roll back.
            version: Version number to restore.

        Returns:
            The restored identity.

        Raises:
            AgentNotFoundError: If agent does not exist.
            ValueError: If version does not exist.
        """
        ...
