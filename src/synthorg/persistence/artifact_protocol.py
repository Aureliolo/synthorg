"""Artifact repository protocol."""

from typing import Protocol, runtime_checkable

from synthorg.core.artifact import Artifact  # noqa: TC001
from synthorg.core.enums import ArtifactType  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT


@runtime_checkable
class ArtifactRepository(Protocol):
    """CRUD + query interface for Artifact persistence."""

    async def save(self, artifact: Artifact) -> bool:
        """Persist an artifact (insert or update) atomically.

        The lifecycle outcome is returned so callers can attach the
        correct ``API_ARTIFACT_CREATED`` / ``API_ARTIFACT_UPDATED``
        audit event without a TOCTOU ``get`` + ``save`` race.

        Args:
            artifact: The artifact to persist.

        Returns:
            ``True`` when this call inserted a new row, ``False`` when
            it updated an existing row in place.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get(self, artifact_id: NotBlankStr) -> Artifact | None:
        """Retrieve an artifact by its ID.

        Args:
            artifact_id: The artifact identifier.

        Returns:
            The artifact, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_artifacts(
        self,
        *,
        task_id: NotBlankStr | None = None,
        created_by: NotBlankStr | None = None,
        artifact_type: ArtifactType | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[Artifact, ...]:
        """List artifacts with optional filters (paginated).

        Results are ordered by artifact ID ascending to ensure
        deterministic pagination across backends.

        Args:
            task_id: Filter by originating task ID.
            created_by: Filter by creator agent ID.
            artifact_type: Filter by artifact type.
            limit: Maximum rows to return (>= 1).
            offset: Rows to skip (>= 0).

        Returns:
            Matching artifacts ordered by ID, as a tuple.

        Raises:
            PersistenceError: If the operation fails.
            QueryError: If ``limit < 1`` or ``offset < 0``.
        """
        ...

    async def delete(self, artifact_id: NotBlankStr) -> bool:
        """Delete an artifact by ID.

        Args:
            artifact_id: The artifact identifier.

        Returns:
            ``True`` if the artifact was deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
