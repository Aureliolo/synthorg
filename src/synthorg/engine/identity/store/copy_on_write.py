"""Copy-on-write identity version store.

Maintains a separate version pointer per agent. Rollback
(``set_current``) only updates the pointer without writing a
new version, making it cheaper but losing the rollback audit
trail that append-only preserves.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.evolution import (
    EVOLUTION_ROLLBACK_TRIGGERED,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.types import NotBlankStr
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.persistence.version_protocol import VersionRepository
    from synthorg.versioning.models import VersionSnapshot
    from synthorg.versioning.service import VersioningService

logger = get_logger(__name__)


class CopyOnWriteIdentityStore:
    """Copy-on-write identity version store.

    Like ``AppendOnlyIdentityStore`` but uses a separate version
    pointer so ``set_current`` does not append a new version.

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
        self._current_version: dict[str, int] = {}
        self._version_lock = asyncio.Lock()

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
        """Store a new identity version and update the pointer."""
        key = str(agent_id)
        async with self._version_lock:
            await self._registry.evolve_identity(
                agent_id,
                identity,
                evolution_rationale=str(saved_by),
            )
            snapshot = await self._versioning.snapshot_if_changed(
                key,
                identity,
                str(saved_by),
            )
            if snapshot is not None:
                self._current_version[key] = snapshot.version
                return snapshot
            latest = await self._versioning.get_latest(key)
            if latest is None:  # pragma: no cover
                msg = f"No version found for agent {agent_id!r} after put"
                raise RuntimeError(msg)
            self._current_version[key] = latest.version
            return latest

    async def get_current(
        self,
        agent_id: NotBlankStr,
    ) -> AgentIdentity | None:
        """Get the current active identity.

        If a version pointer exists, resolves via the pointer.
        Otherwise falls back to the registry.
        """
        key = str(agent_id)
        async with self._version_lock:
            version = self._current_version.get(key)
            if version is not None:
                snapshot = await self._repo.get_version(key, version)
                if snapshot is not None:
                    return snapshot.snapshot
        return await self._registry.get(agent_id)

    async def get_version(
        self,
        agent_id: NotBlankStr,
        version: int,
    ) -> AgentIdentity | None:
        """Get a specific identity version."""
        snapshot = await self._repo.get_version(str(agent_id), version)
        if snapshot is None:
            return None
        return snapshot.snapshot

    async def list_versions(
        self,
        agent_id: NotBlankStr,
    ) -> tuple[VersionSnapshot[AgentIdentity], ...]:
        """List all identity versions (newest first)."""
        return await self._repo.list_versions(str(agent_id))

    async def set_current(
        self,
        agent_id: NotBlankStr,
        version: int,
    ) -> AgentIdentity:
        """Set the version pointer to a specific version (rollback).

        Does NOT append a new version -- only updates the pointer
        and the registry's current identity.

        Args:
            agent_id: Agent to roll back.
            version: Version number to restore.

        Returns:
            The restored identity.

        Raises:
            AgentNotFoundError: If agent does not exist.
            ValueError: If version does not exist.
        """
        key = str(agent_id)
        snapshot = await self._repo.get_version(key, version)
        if snapshot is None:
            msg = f"Version {version} not found for agent {agent_id!r}"
            raise ValueError(msg)

        restored = snapshot.snapshot
        async with self._version_lock:
            await self._registry.evolve_identity(
                agent_id,
                restored,
                evolution_rationale=f"rollback to version {version}",
            )
            self._current_version[key] = version
        logger.info(
            EVOLUTION_ROLLBACK_TRIGGERED,
            agent_id=key,
            restored_version=version,
        )
        return restored
