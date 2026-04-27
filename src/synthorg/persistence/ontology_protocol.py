"""Ontology repository protocols.

Replaces the old parallel ``OntologyBackend`` abstraction from
``synthorg.ontology.protocol``.  The same method surface (register,
get, update, delete, list_entities, search, get_version_manifest)
is now provided by the persistence-layer repository; lifecycle
methods (``connect`` / ``disconnect`` / ``health_check`` /
``is_connected`` / ``get_db``) belong to :class:`PersistenceBackend`.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.ontology.models import (
        DriftReport,
        EntityDefinition,
        EntityTier,
    )


@runtime_checkable
class OntologyEntityRepository(Protocol):
    """CRUD + search interface for entity definitions."""

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier."""
        ...

    async def register(self, entity: EntityDefinition) -> None:
        """Register a new entity definition.

        Raises:
            OntologyDuplicateError: If an entity with that name exists.
        """
        ...

    async def get(self, name: str) -> EntityDefinition:
        """Retrieve an entity definition by name.

        Raises:
            OntologyNotFoundError: If no such entity exists.
        """
        ...

    async def update(self, entity: EntityDefinition) -> None:
        """Update an existing entity definition (matched by name).

        Raises:
            OntologyNotFoundError: If no such entity exists.
        """
        ...

    async def delete(self, name: str) -> None:
        """Delete an entity definition by name.

        Raises:
            OntologyNotFoundError: If no such entity exists.
        """
        ...

    async def list_entities(
        self,
        *,
        tier: EntityTier | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[EntityDefinition, ...]:
        """List all entity definitions, optionally filtered by tier."""
        ...

    async def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[EntityDefinition, ...]:
        """Substring search against entity name and definition text."""
        ...

    async def get_version_manifest(self) -> dict[NotBlankStr, int]:
        """Return the latest version number for each entity."""
        ...


# Alias for callers that still type-hint against the old name.  The
# old ``OntologyBackend`` carried lifecycle methods; those have moved
# to :class:`PersistenceBackend` and callers who need them reach
# through the shared backend instead.
OntologyBackend = OntologyEntityRepository


@runtime_checkable
class OntologyDriftReportRepository(Protocol):
    """Storage protocol for drift detection reports."""

    async def store_report(self, report: DriftReport) -> None:
        """Persist a drift report."""
        ...

    async def get_latest(
        self,
        entity_name: NotBlankStr,
        *,
        limit: int = 10,
    ) -> tuple[DriftReport, ...]:
        """Return most recent drift reports for an entity."""
        ...

    async def get_all_latest(
        self,
        *,
        limit: int = 100,
    ) -> tuple[DriftReport, ...]:
        """Return the most recent drift report for each entity."""
        ...
