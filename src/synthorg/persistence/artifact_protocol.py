"""Artifact repository protocol."""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.artifact import Artifact
from synthorg.core.enums import ArtifactType  # noqa: TC001,RUF100
from synthorg.core.types import NotBlankStr  # noqa: TC001,RUF100
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    IdKeyedRepository,
)

__all__ = [
    "ArtifactFilterSpec",
    "ArtifactRepository",
]


class ArtifactFilterSpec(BaseModel):
    """Filter spec for ``ArtifactRepository.query`` (ADR-0001)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    task_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by originating task ID",
    )
    created_by: NotBlankStr | None = Field(
        default=None,
        description="Filter by creator agent ID",
    )
    artifact_type: ArtifactType | None = Field(
        default=None,
        description="Filter by artifact type",
    )


@runtime_checkable
class ArtifactRepository(
    IdKeyedRepository[Artifact, NotBlankStr],
    FilteredQueryRepository[Artifact, ArtifactFilterSpec],
    Protocol,
):
    """CRUD + query interface for Artifact persistence.

    Composes :class:`IdKeyedRepository` + :class:`FilteredQueryRepository`
    (ADR-0001).

    The single bespoke method :meth:`save_returning_outcome` is retained
    (D7 bespoke-method policy) because callers need the insert/update
    outcome to attach the correct ``API_ARTIFACT_CREATED`` /
    ``API_ARTIFACT_UPDATED`` audit event without a TOCTOU ``get`` + ``save``
    race. This avoids the rare but real bug where concurrent writers both
    observe "missing" and both report ``API_ARTIFACT_CREATED``.
    """

    async def save(self, entity: Artifact) -> None:
        """Persist an artifact (insert or update by id).

        Args:
            entity: The artifact to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def save_returning_outcome(self, artifact: Artifact) -> bool:
        """Persist an artifact atomically and return the insert/update outcome.

        This is a D7 bespoke method retained for the audit-event correctness
        invariant: callers need to know whether the write was an insert or
        update so they can emit the corresponding ``API_ARTIFACT_CREATED`` or
        ``API_ARTIFACT_UPDATED`` event. The outcome is computed atomically
        with the write (SQLite: ``INSERT ... ON CONFLICT(id) DO NOTHING``
        rowcount; Postgres: ``xmax = 0 AS created``) to avoid the TOCTOU
        window of a separate ``get()`` probe.

        Args:
            artifact: The artifact to persist.

        Returns:
            ``True`` when this call inserted a new row, ``False`` when
            it updated an existing row in place.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get(self, entity_id: NotBlankStr) -> Artifact | None:
        """Retrieve an artifact by its ID.

        Args:
            entity_id: The artifact identifier.

        Returns:
            The artifact, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Artifact, ...]:
        """List all artifacts with pagination.

        Results are ordered by artifact ID ascending to ensure
        deterministic pagination across backends.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Artifacts ordered by id ascending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def query(
        self,
        filter_spec: ArtifactFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Artifact, ...]:
        """List artifacts matching the filter spec (paginated).

        Results are ordered by artifact ID ascending to ensure
        deterministic pagination across backends.

        Args:
            filter_spec: Carries optional filters for task_id, created_by,
                artifact_type.
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Matching artifacts ordered by ID, as a tuple.

        Raises:
            PersistenceError: If the operation fails.
            QueryError: If ``limit < 1`` or ``offset < 0``.
        """
        ...

    async def count(self, filter_spec: ArtifactFilterSpec) -> int:
        """Count artifacts matching the filter spec.

        Args:
            filter_spec: Carries optional filters.

        Returns:
            Total number of matching artifacts.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete an artifact by ID.

        Args:
            entity_id: The artifact identifier.

        Returns:
            ``True`` if the artifact was deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
