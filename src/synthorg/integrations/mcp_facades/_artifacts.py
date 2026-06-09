# ruff: noqa: EM101, PLR0913
# module-kind: service
"""Artifact-index facade over ``ArtifactStorageBackend``."""

import asyncio
import copy
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    ARTIFACT_CREATED_VIA_MCP,
    ARTIFACT_DELETED_VIA_MCP,
)

if TYPE_CHECKING:
    # ArtifactStorageBackend is a runtime_checkable protocol injected via a
    # SimpleNamespace fake; a runtime import would make typeguard reject it.
    from synthorg.persistence.artifact_storage import ArtifactStorageBackend

logger = get_logger(__name__)


class _ArtifactRecord:
    """In-memory index entry for one stored artifact."""

    __slots__ = (
        "content_type",
        "created_at",
        "id",
        "name",
        "size_bytes",
        "storage_ref",
    )

    def __init__(
        self,
        *,
        id: UUID,  # noqa: A002
        name: str,
        content_type: str,
        size_bytes: int,
        storage_ref: str,
        created_at: datetime,
    ) -> None:
        self.id = id
        self.name = name
        self.content_type = content_type
        self.size_bytes = size_bytes
        self.storage_ref = storage_ref
        self.created_at = created_at

    def to_dict(self) -> dict[str, object]:
        """Serialise the artifact record to a JSON-safe dict.

        Returns:
            A dict of the artifact's ID, name, content type, size,
            storage ref, and ISO-formatted creation timestamp.
        """
        return {
            "id": str(self.id),
            "name": self.name,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "storage_ref": self.storage_ref,
            "created_at": self.created_at.isoformat(),
        }


class ArtifactFacadeService:
    """Facade over :class:`ArtifactStorageBackend`.

    Reads and writes wrap the storage primitive; listing and metadata
    lookups use an in-memory index populated on create so that MCP
    clients can browse artifacts without touching durable storage for
    every request.
    """

    def __init__(self, *, storage: ArtifactStorageBackend) -> None:
        self._storage = cast("object", storage)
        self._index: dict[UUID, _ArtifactRecord] = {}
        self._lock = asyncio.Lock()

    async def list_artifacts(self) -> Sequence[_ArtifactRecord]:
        """List indexed artifacts, newest-first.

        Returns:
            A tuple of deep-copied artifact records ordered by creation
            time (most recent first).
        """
        async with self._lock:
            snapshot = tuple(copy.deepcopy(a) for a in self._index.values())
        return tuple(sorted(snapshot, key=lambda a: a.created_at, reverse=True))

    async def get_artifact(self, artifact_id: NotBlankStr) -> _ArtifactRecord | None:
        """Fetch an artifact's index record by ID.

        Returns:
            A deep copy of the matching record, or ``None`` when
            ``artifact_id`` is not a valid UUID or no record matches.
        """
        try:
            key = UUID(artifact_id)
        except ValueError:
            return None
        async with self._lock:
            record = self._index.get(key)
            return copy.deepcopy(record) if record is not None else None

    async def create_artifact(
        self,
        *,
        name: NotBlankStr,
        content_type: NotBlankStr,
        size_bytes: int,
        storage_ref: NotBlankStr,
        actor_id: NotBlankStr,
    ) -> _ArtifactRecord:
        """Index a stored artifact, auditing the event.

        Returns:
            A deep copy of the newly created ``_ArtifactRecord``.
        """
        record = _ArtifactRecord(
            id=uuid4(),
            name=name,
            content_type=content_type,
            size_bytes=size_bytes,
            storage_ref=storage_ref,
            created_at=datetime.now(UTC),
        )
        async with self._lock:
            self._index[record.id] = record
        logger.info(
            ARTIFACT_CREATED_VIA_MCP,
            artifact_id=str(record.id),
            actor_id=actor_id,
            size_bytes=size_bytes,
        )
        return copy.deepcopy(record)

    async def delete_artifact(
        self,
        *,
        artifact_id: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> bool:
        """Delete an artifact from storage and the index, auditing it.

        Returns:
            ``True`` when the artifact was removed from both storage and
            the index; ``False`` when ``artifact_id`` is not a valid
            UUID, no record matches, or the storage delete reports a miss.

        Raises:
            CapabilityNotSupportedError: If the storage backend does not
                expose ``delete`` (refusing to orphan the blob).
        """
        try:
            key = UUID(artifact_id)
        except ValueError:
            return False
        # Serialise the index read + storage delete + index pop so two
        # concurrent deletes of the same artifact cannot race: without
        # the lock, both coroutines could read the same record, both
        # call ``storage.delete`` (potentially raising for the second),
        # and only one ``pop`` would succeed while the other logs a
        # spurious success.
        async with self._lock:
            record = self._index.get(key)
            if record is None:
                return False
            fn = getattr(self._storage, "delete", None)
            if not callable(fn):
                raise CapabilityNotSupportedError(
                    "artifact_delete",
                    "ArtifactStorageBackend does not expose delete; refusing "
                    "to drop the index entry silently since the blob would be "
                    "orphaned.",
                )
            # Delete from storage FIRST so the index and storage cannot
            # diverge silently -- if storage fails, the record stays in
            # the index and the caller sees the real error.  Use the
            # backend's own storage_ref, not the facade UUID, because the
            # two diverge when the storage backend uses its own scheme.
            # Treat a falsy return (e.g. ``False`` for "not found" in the
            # backend) as an actual miss: don't drop the index entry or
            # log a fake success.
            storage_removed = await fn(record.storage_ref)
            # Any falsy return (``False``, ``None``, ``0``) is treated
            # as a miss so the index entry stays put and no audit
            # event fires; only a truthy confirmation drops the row.
            if not storage_removed:
                return False
            self._index.pop(key, None)
        logger.info(
            ARTIFACT_DELETED_VIA_MCP,
            artifact_id=artifact_id,
            actor_id=actor_id,
            reason=reason,
            removed=True,
        )
        return True
