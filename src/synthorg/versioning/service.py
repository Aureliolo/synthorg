"""Generic versioning service.

Wraps a :class:`~synthorg.persistence.version_protocol.VersionRepository`
to provide content-addressable snapshot creation: a new version is only
persisted when the entity content has actually changed.
"""

import copy
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

from synthorg.observability import get_logger
from synthorg.observability.events.versioning import (
    VERSION_SAVED,
    VERSION_SNAPSHOT_SKIPPED,
)
from synthorg.versioning.hashing import compute_content_hash
from synthorg.versioning.models import VersionSnapshot

if TYPE_CHECKING:
    from synthorg.persistence.version_protocol import VersionRepository

logger = get_logger(__name__)


class VersioningService[T: BaseModel]:
    """Content-aware versioning service for Pydantic model entities.

    A new :class:`~synthorg.versioning.models.VersionSnapshot` is
    persisted only when the entity content has changed (determined by
    comparing SHA-256 content hashes).  Identical content produces a
    no-op, avoiding version inflation from spurious no-change saves.

    The version number is computed as ``latest.version + 1`` (or 1 if
    no versions exist).  The repository uses ``INSERT OR IGNORE``
    semantics, so concurrent saves of the same version number are safe:
    the second write is silently dropped.  This trade-off is acceptable
    because identity-like entities are updated at low frequency.

    Args:
        repo: Repository managing the entity's version snapshots.
    """

    def __init__(self, repo: VersionRepository[T]) -> None:
        self._repo = repo

    async def snapshot_if_changed(
        self,
        entity_id: str,
        snapshot: T,
        saved_by: str,
    ) -> VersionSnapshot[T] | None:
        """Persist a new version only when content has changed.

        Computes the SHA-256 hash of the snapshot, fetches the latest
        persisted version, and skips persistence when the hashes match.

        Args:
            entity_id: String primary key of the entity being versioned.
            snapshot: The current entity state to snapshot.
            saved_by: Identifier of the actor triggering the snapshot.

        Returns:
            The newly created :class:`VersionSnapshot` if content
            changed, or ``None`` if content was unchanged.

        Raises:
            PersistenceError: If the repository operation fails.
        """
        # Freeze the snapshot before any awaits so that shared mutable
        # models cannot change between hash computation and persistence.
        frozen = copy.deepcopy(snapshot)
        content_hash = compute_content_hash(frozen)
        latest = await self._repo.get_latest_version(entity_id)

        if latest is not None and latest.content_hash == content_hash:
            logger.debug(
                VERSION_SNAPSHOT_SKIPPED,
                entity_id=entity_id,
                content_hash=content_hash,
                current_version=latest.version,
            )
            return None

        version = self._build_snapshot(
            entity_id,
            latest,
            content_hash,
            frozen,
            saved_by,
        )
        inserted = await self._repo.save_version(version)
        if not inserted:
            return await self._resolve_conflict(
                entity_id,
                content_hash,
                frozen,
                saved_by,
            )
        logger.info(
            VERSION_SAVED,
            entity_id=entity_id,
            version=version.version,
            saved_by=saved_by,
        )
        return version

    def _build_snapshot(
        self,
        entity_id: str,
        latest: VersionSnapshot[T] | None,
        content_hash: str,
        frozen: T,
        saved_by: str,
    ) -> VersionSnapshot[T]:
        """Build a VersionSnapshot with the next version number.

        Returns:
            A new ``VersionSnapshot`` at ``latest.version + 1`` (or version 1
            when there is no prior snapshot), carrying the frozen content and
            its content hash.
        """
        new_version = (latest.version + 1) if latest is not None else 1
        return VersionSnapshot(
            entity_id=entity_id,
            version=new_version,
            content_hash=content_hash,
            snapshot=frozen,
            saved_by=saved_by,
            saved_at=datetime.now(UTC),
        )

    async def _resolve_conflict(
        self,
        entity_id: str,
        content_hash: str,
        frozen: T,
        saved_by: str,
    ) -> VersionSnapshot[T] | None:
        """Handle a concurrent-write conflict on version number.

        Re-fetches the persisted row and returns it if the content
        matches.  If different content won the race, builds a new
        snapshot at the next version number and retries once.

        Returns:
            The persisted snapshot when its content matches the local write,
            the freshly inserted retry snapshot when the second attempt wins,
            or the latest persisted snapshot after a second collision.
            ``None`` only when no snapshot exists for the entity.
        """
        persisted = await self._repo.get_latest_version(entity_id)
        if persisted is not None and persisted.content_hash == content_hash:
            return persisted
        # Different content won the race -- retry with next version.
        retry = self._build_snapshot(
            entity_id,
            persisted,
            content_hash,
            frozen,
            saved_by,
        )
        inserted = await self._repo.save_version(retry)
        if inserted:
            logger.info(
                VERSION_SAVED,
                entity_id=entity_id,
                version=retry.version,
                saved_by=saved_by,
            )
            return retry
        # Second collision (extremely unlikely). Return persisted state.
        return await self._repo.get_latest_version(entity_id)

    async def force_snapshot(
        self,
        entity_id: str,
        snapshot: T,
        saved_by: str,
    ) -> VersionSnapshot[T]:
        """Persist a snapshot regardless of content change.

        Unlike ``snapshot_if_changed``, this always creates a new version
        even if the content is identical to the latest version. Useful for
        audit trails (e.g., recording rollbacks when content is unchanged).

        Args:
            entity_id: String primary key of the entity being versioned.
            snapshot: The current entity state to snapshot.
            saved_by: Identifier of the actor triggering the snapshot.

        Returns:
            The newly created :class:`VersionSnapshot`.

        Raises:
            PersistenceError: If the repository operation fails.
        """
        frozen = copy.deepcopy(snapshot)
        content_hash = compute_content_hash(frozen)
        latest = await self._repo.get_latest_version(entity_id)

        version = self._build_snapshot(
            entity_id,
            latest,
            content_hash,
            frozen,
            saved_by,
        )
        inserted = await self._repo.save_version(version)
        if not inserted:
            return (
                await self._resolve_conflict(
                    entity_id,
                    content_hash,
                    frozen,
                    saved_by,
                )
                or version
            )
        logger.info(
            VERSION_SAVED,
            entity_id=entity_id,
            version=version.version,
            saved_by=saved_by,
        )
        return version

    async def get_latest(
        self,
        entity_id: str,
    ) -> VersionSnapshot[T] | None:
        """Retrieve the most recent version snapshot for an entity.

        Convenience delegating to the underlying repository.

        Args:
            entity_id: String primary key of the entity.

        Returns:
            The latest :class:`VersionSnapshot`, or ``None`` if none exist.

        Raises:
            PersistenceError: If the repository operation fails.
        """
        return await self._repo.get_latest_version(entity_id)
