"""Generic repository protocol for versioned entity persistence."""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel

from synthorg.core.types import NotBlankStr  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.versioning.models import VersionSnapshot


@runtime_checkable
class VersionRepository[T: BaseModel](Protocol):
    """CRUD interface for versioned entity snapshots.

    Version records are immutable once created -- they capture the
    exact state of an entity at a specific point in time.  The
    ``save_version`` method uses ``INSERT OR IGNORE`` semantics for
    idempotency.

    Implementations must parameterise ``T`` with the concrete entity
    type they manage (e.g., ``VersionRepository[AgentIdentity]``).
    """

    async def save_version(self, version: VersionSnapshot[T]) -> bool:
        """Persist a version snapshot (insert only, idempotent).

        Uses ``INSERT OR IGNORE`` semantics: a second save of the same
        ``(entity_id, version)`` pair is silently dropped rather than
        raising an error.

        Args:
            version: The version snapshot to persist.

        Returns:
            ``True`` if the row was actually inserted, ``False`` if it
            was already present (duplicate silently ignored).

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_version(
        self,
        entity_id: NotBlankStr,
        version: int,
    ) -> VersionSnapshot[T] | None:
        """Retrieve a specific version snapshot.

        Args:
            entity_id: The entity's string primary key.
            version: The version number.

        Returns:
            The version snapshot, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_latest_version(
        self,
        entity_id: NotBlankStr,
    ) -> VersionSnapshot[T] | None:
        """Retrieve the most recent version snapshot for an entity.

        Args:
            entity_id: The entity's string primary key.

        Returns:
            The latest version snapshot, or ``None`` if none exist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_by_content_hash(
        self,
        entity_id: NotBlankStr,
        content_hash: NotBlankStr,
    ) -> VersionSnapshot[T] | None:
        """Retrieve a version by its content hash.

        Useful for content-addressable deduplication: if the hash
        already exists, no new version is needed.

        Args:
            entity_id: The entity's string primary key.
            content_hash: The SHA-256 hex digest to look up.

        Returns:
            The matching version snapshot, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_versions(
        self,
        entity_id: NotBlankStr,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[VersionSnapshot[T], ...]:
        """List version snapshots for an entity.

        Results are ordered by version descending (newest first).

        Args:
            entity_id: The entity's string primary key.
            limit: Maximum number of results.
            offset: Number of results to skip.

        Returns:
            Matching version snapshots as a tuple.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def count_versions(self, entity_id: NotBlankStr) -> int:
        """Count version snapshots for an entity.

        Args:
            entity_id: The entity's string primary key.

        Returns:
            Number of version records for the entity.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete_versions_for_entity(self, entity_id: NotBlankStr) -> int:
        """Delete all version snapshots for an entity.

        Args:
            entity_id: The entity's string primary key.

        Returns:
            Number of deleted records.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
