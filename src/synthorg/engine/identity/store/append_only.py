"""Append-only identity version store.

Every identity mutation creates a new version snapshot. Rollback
(``set_current``) writes a *new* version pointing to the restored
snapshot content, preserving the full audit trail.
"""

from synthorg.core.agent import AgentIdentity
from synthorg.core.types import NotBlankStr
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability import get_logger
from synthorg.observability.events.evolution import (
    EVOLUTION_ROLLBACK_TRIGGERED,
)
from synthorg.persistence.version_protocol import VersionRepository
from synthorg.versioning.models import VersionSnapshot
from synthorg.versioning.service import VersioningService

logger = get_logger(__name__)


class AppendOnlyIdentityStore:
    """Append-only identity version store.

    Wraps ``AgentRegistryService`` for current-identity management
    and ``VersioningService[AgentIdentity]`` for version persistence.
    Every ``put`` and ``set_current`` call appends a new version;
    no version is ever overwritten or deleted.

    Args:
        registry: Agent registry for current-identity CRUD.
        versioning: Versioning service for snapshot persistence.
    """

    def __init__(
        self,
        *,
        registry: AgentRegistryService,
        versioning: VersioningService[AgentIdentity],
    ) -> None:
        self._registry = registry
        self._versioning = versioning

    @property
    def _repo(self) -> VersionRepository[AgentIdentity]:
        """Access the underlying version repository."""
        return self._versioning._repo  # noqa: SLF001

    async def put(
        self,
        agent_id: NotBlankStr,
        identity: AgentIdentity,
        *,
        saved_by: NotBlankStr,
    ) -> VersionSnapshot[AgentIdentity]:
        """Store a new identity version.

        Updates the registry's current identity and persists a
        versioned snapshot. If the content is unchanged from the
        latest version, a snapshot is still forced to record the
        ``saved_by`` actor.

        Args:
            agent_id: Agent to update.
            identity: New identity state.
            saved_by: Actor triggering the change.

        Returns:
            The created version snapshot.

        Raises:
            AgentNotFoundError: If agent does not exist.
            PersistenceError: If versioning fails.
            RuntimeError: When the versioning seam returns ``None``
                even though no prior snapshot exists (defensive
                guard; should not happen in practice).
        """
        snapshot = await self._versioning.snapshot_if_changed(
            str(agent_id),
            identity,
            str(saved_by),
        )
        if snapshot is None:
            # Content unchanged -- get latest existing version.
            latest = await self._versioning.get_latest(str(agent_id))
            if latest is None:  # pragma: no cover -- should not happen
                msg = f"No version found for agent {agent_id!r} after put"
                raise RuntimeError(msg)
            snapshot = latest
        await self._registry.evolve_identity(
            agent_id,
            identity,
            evolution_rationale=str(saved_by),
        )
        return snapshot

    async def get_current(
        self,
        agent_id: NotBlankStr,
    ) -> AgentIdentity | None:
        """Get the current active identity from the registry.

        Returns:
            The current :class:`AgentIdentity` when the agent exists;
            ``None`` otherwise.
        """
        return await self._registry.get(agent_id)

    async def get_version(
        self,
        agent_id: NotBlankStr,
        version: int,
    ) -> AgentIdentity | None:
        """Get a specific identity version.

        Returns:
            The :class:`AgentIdentity` stored at ``version``; ``None``
            when no matching snapshot exists.
        """
        snapshot = await self._repo.get_version(str(agent_id), version)
        if snapshot is None:
            return None
        return snapshot.snapshot

    async def list_versions(
        self,
        agent_id: NotBlankStr,
    ) -> tuple[VersionSnapshot[AgentIdentity], ...]:
        """List all identity versions (newest first).

        Returns:
            Tuple of :class:`VersionSnapshot` records in newest-first
            order; ``()`` when no versions exist.
        """
        return await self._repo.list_versions(str(agent_id))

    async def set_current(
        self,
        agent_id: NotBlankStr,
        version: int,
    ) -> AgentIdentity:
        """Roll back to a specific identity version.

        Fetches the snapshot at the given version and applies it
        as the current identity. A new version is appended (audit
        trail records the rollback).

        Args:
            agent_id: Agent to roll back.
            version: Version number to restore.

        Returns:
            The restored identity.

        Raises:
            AgentNotFoundError: If agent does not exist.
            ValueError: If version does not exist.
        """
        snapshot = await self._repo.get_version(str(agent_id), version)
        if snapshot is None:
            msg = f"Version {version} not found for agent {agent_id!r}"
            raise ValueError(msg)

        restored = snapshot.snapshot
        await self._registry.evolve_identity(
            agent_id,
            restored,
            evolution_rationale=f"rollback to version {version}",
        )
        logger.info(
            EVOLUTION_ROLLBACK_TRIGGERED,
            agent_id=str(agent_id),
            restored_version=version,
        )
        # Force a new version recording the rollback, even if content is
        # already in registry (ensures audit trail is complete).
        await self._versioning.force_snapshot(
            str(agent_id),
            restored,
            f"rollback:v{version}",
        )
        return restored
