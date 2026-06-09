"""Bespoke repository protocol for versioned entity persistence."""

from typing import Final, Protocol, runtime_checkable

from pydantic import BaseModel

from synthorg.core.types import NotBlankStr
from synthorg.versioning.models import VersionSnapshot

#: Default limit for list_versions pagination.
_DEFAULT_LIST_LIMIT_50: Final[int] = 50


@runtime_checkable
class VersionRepository[T: BaseModel](Protocol):
    """Bespoke CRUD interface for versioned entity snapshots.

    Version records are immutable once created -- they capture the
    exact state of an entity at a specific point in time. The composite
    key is ``(entity_id, version)`` tuple. The ``save_version`` method
    uses ``INSERT OR IGNORE`` semantics for idempotency.

    Bespoke per ADR-0001 D7:

    * Composite-key (entity_id, version) with int second component differs
      from typical str-keyed CRUD and does not fit IdKeyedRepository cleanly.
    * ``get_latest_version`` returns the single newest row by version
      for an entity; the generic ``query`` + sorting cannot express the
      ``LIMIT 1 ORDER BY version DESC`` shape efficiently.
    * ``get_by_content_hash`` is an alternate-key lookup on the
      ``content_hash`` column for content-addressable deduplication;
      routing through a generic filter is wasteful for a targeted lookup.
    * ``delete_versions_for_entity`` is a bulk delete keyed on
      ``entity_id`` scope (all versions for one entity); the generic
      ``delete((entity_id, version))`` deletes only one row at a time.

    These constraints do not compose cleanly into FilteredQueryRepository
    or IdKeyedRepository, so VersionRepository remains completely bespoke.

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
        """Retrieve a specific version snapshot by composite key.

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

        Bespoke method (D7): more efficient than a generic query for this
        common case.

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

        Bespoke method (D7): alternate-key lookup for content-addressable
        deduplication.

        Useful for deduplication: if the hash already exists, no new
        version is needed.

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
        limit: int = _DEFAULT_LIST_LIMIT_50,
        offset: int = 0,
    ) -> tuple[VersionSnapshot[T], ...]:
        """List version snapshots for an entity with pagination.

        Results are ordered by version descending (newest first).

        Args:
            entity_id: The entity's string primary key.
            limit: Maximum number of results (default 50).
            offset: Number of results to skip (default 0).

        Returns:
            Version snapshots as a tuple.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def count_versions(self, entity_id: NotBlankStr) -> int:
        """Count version snapshots for an entity.

        Args:
            entity_id: The entity's string primary key.

        Returns:
            Total number of versions for this entity.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete_versions_for_entity(self, entity_id: NotBlankStr) -> int:
        """Delete all version snapshots for an entity.

        Bespoke method (D7): bulk delete by entity scope. The generic
        ``delete((entity_id, version))`` deletes only one row at a time,
        making this batch operation inefficient to express generically.

        Args:
            entity_id: The entity's string primary key.

        Returns:
            Number of deleted records.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
