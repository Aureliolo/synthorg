"""ArtifactStorageBackend protocol -- pluggable content storage.

Artifact metadata lives in the persistence backend (SQLite); artifact
*content bytes* are handled by this pluggable storage backend.
Follows the same protocol pattern as ``MemoryBackend`` and
``PersistenceBackend``.

Listing artifacts is a metadata concern handled by
``ArtifactRepository.list_artifacts()``, not the content storage layer.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr


@runtime_checkable
class ArtifactStorageBackend(Protocol):
    """Pluggable storage backend for artifact content bytes.

    Implementations handle the physical storage of binary content,
    while the ``ArtifactRepository`` handles metadata persistence.

    Attributes:
        backend_name: Human-readable backend identifier.
    """

    @property
    def backend_name(self) -> str:
        """Human-readable backend identifier (e.g. ``"filesystem"``)."""
        ...

    async def store(self, artifact_id: NotBlankStr, content: bytes) -> int:
        """Store artifact content.

        Args:
            artifact_id: Unique artifact identifier.
            content: Binary content to store.

        Returns:
            Number of bytes written.

        Raises:
            ArtifactTooLargeError: If *content* exceeds the maximum
                single-artifact size.
            ArtifactStorageFullError: If storing would exceed the total
                storage capacity.
        """
        ...

    async def retrieve(self, artifact_id: NotBlankStr) -> bytes:
        """Retrieve artifact content.

        Args:
            artifact_id: Unique artifact identifier.

        Returns:
            The stored binary content.

        Raises:
            RecordNotFoundError: If no content exists for the given ID.
        """
        ...

    async def delete(self, artifact_id: NotBlankStr) -> bool:
        """Delete artifact content.

        Args:
            artifact_id: Unique artifact identifier.

        Returns:
            ``True`` if content was deleted, ``False`` if not found.
        """
        ...

    async def exists(self, artifact_id: NotBlankStr) -> bool:
        """Check whether content exists for an artifact.

        Args:
            artifact_id: Unique artifact identifier.

        Returns:
            ``True`` if content exists.
        """
        ...

    async def total_size(self) -> int:
        """Return total bytes stored across all artifacts.

        Returns:
            Total storage usage in bytes.
        """
        ...
