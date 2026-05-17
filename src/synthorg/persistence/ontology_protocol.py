"""Ontology repository protocols.

Replaces the old parallel ``OntologyBackend`` abstraction from
``synthorg.ontology.protocol``.  The same method surface (register,
get, update, delete, list_entities, search, get_version_manifest)
is now provided by the persistence-layer repository; lifecycle
methods (``connect`` / ``disconnect`` / ``health_check`` /
``is_connected`` / ``get_db``) belong to :class:`PersistenceBackend`.
"""

from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    AppendOnlyRepository,
    IdKeyedRepository,
)
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.ontology.models import (
        DriftReport,
        EntityDefinition,
        EntityTier,
    )

_DEFAULT_DRIFT_REPORTS_LIMIT: Final[int] = 10


class DriftReportFilterSpec(BaseModel):
    """Filter specification for drift report queries.

    Placeholder for future drift report filtering. All filtered-query
    repositories define a frozen Pydantic model for their filter
    arguments, even when empty.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


@runtime_checkable
class OntologyEntityRepository(
    IdKeyedRepository["EntityDefinition", "NotBlankStr"],
    Protocol,
):
    """CRUD + search interface for entity definitions.

    Composes :class:`IdKeyedRepository`. :meth:`register` and
    :meth:`update` encode distinct audit semantics (register fails on
    duplicate, update fails on missing) that callers depend on and the
    generic ``save`` cannot express. :meth:`list_entities`,
    :meth:`search`, and :meth:`get_version_manifest` are domain-specific
    queries beyond generic ``list_items``.
    """

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier."""
        ...

    async def save(self, entity: EntityDefinition) -> None:
        """Insert or update an entity (idempotent upsert).

        Implements the :class:`IdKeyedRepository` upsert contract: a
        repeated ``save`` for an existing entity name updates the row
        rather than raising. :meth:`register` (insert-only) remains
        available for callers that need duplicate detection.

        Args:
            entity: The entity definition to persist.

        Raises:
            OntologyError: If the underlying write fails.
        """
        ...

    async def get(self, entity_id: NotBlankStr) -> EntityDefinition | None:
        """Retrieve an entity definition by name.

        Returns ``None`` when absent (generic ``IdKeyedRepository``
        contract). Callers that need a raised error on a missing entity
        use ``register`` or ``update`` (which raise) rather than
        treating a ``None`` return as exceptional.

        Args:
            entity_id: The entity name (entity id is the name).

        Returns:
            The entity definition, or ``None`` if not found.
        """
        ...

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete an entity definition by name.

        Returns ``True`` iff a row existed (generic ``IdKeyedRepository`` contract).

        Args:
            entity_id: The entity name (entity id is the name).

        Returns:
            ``True`` if deleted, ``False`` if not found.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[EntityDefinition, ...]:
        """List all entity definitions in name order.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Entity definitions in ascending name order.
        """
        ...

    async def register(self, entity: EntityDefinition) -> None:
        """Register a new entity definition.

        Distinct audit semantics from the generic ``save``: callers
        that need insert-or-fail semantics (detecting duplicates) use
        this instead.

        Args:
            entity: The entity definition to register.

        Raises:
            OntologyDuplicateError: If an entity with that name exists.
        """
        ...

    async def update(self, entity: EntityDefinition) -> None:
        """Update an existing entity definition (matched by name).

        Distinct audit semantics from the generic ``save``: callers
        that need update-or-fail semantics (detecting missing entities)
        use this instead.

        Args:
            entity: The entity definition to update.

        Raises:
            OntologyNotFoundError: If no such entity exists.
        """
        ...

    async def list_entities(
        self,
        *,
        tier: EntityTier | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[EntityDefinition, ...]:
        """List all entity definitions, optionally filtered by tier.

        Tier-based filtering is a domain invariant.

        Args:
            tier: Optional tier to filter by.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Entity definitions in ascending name order.
        """
        ...

    async def search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[EntityDefinition, ...]:
        """Substring search against entity name and definition text.

        Full-text search is a domain-specific operation.

        Args:
            query: Search string to match against name or definition.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Matching entity definitions in ascending name order.
        """
        ...

    async def get_version_manifest(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> dict[NotBlankStr, int]:
        """Return a bounded page of the latest version per entity.

        Version manifest is a domain-specific aggregate. Entities page
        in ``entity_id`` order so a cursor walk is stable.

        Args:
            limit: Maximum entries to return.
            offset: Entries to skip from the head of the ordering.

        Returns:
            A page of the entity-name to latest-version mapping.
            Callers needing the whole manifest drain via
            :func:`synthorg.persistence._shared.collect_all_mapping`.
        """
        ...


# Alias for callers that still type-hint against the old name.  The
# old ``OntologyBackend`` carried lifecycle methods; those have moved
# to :class:`PersistenceBackend` and callers who need them reach
# through the shared backend instead.
OntologyBackend = OntologyEntityRepository


@runtime_checkable
class OntologyDriftReportRepository(
    AppendOnlyRepository["DriftReport", DriftReportFilterSpec],
    Protocol,
):
    """Storage protocol for drift detection reports.

    Composes :class:`AppendOnlyRepository`. :meth:`get_latest`
    (per-entity most recent) and :meth:`get_all_latest` (latest per
    entity across all entities) are domain-specific aggregates beyond
    the generic ``query`` surface and are optimised with specialised
    SQL (indexes on ``(entity_name, id DESC)`` and ``DISTINCT ON``
    subqueries for fast per-entity selection).
    """

    async def append(self, event: DriftReport) -> None:
        """Append one drift report (write-only; reports are immutable once written).

        Args:
            event: The drift report to persist.
        """
        ...

    async def get_latest(
        self,
        entity_name: NotBlankStr,
        *,
        limit: int = _DEFAULT_DRIFT_REPORTS_LIMIT,
    ) -> tuple[DriftReport, ...]:
        """Return most recent drift reports for an entity.

        Optimised query for per-entity most-recent reports.

        Args:
            entity_name: Entity to retrieve reports for.
            limit: Maximum reports to return (default 10).

        Returns:
            Drift reports for the entity in descending creation order.
        """
        ...

    async def get_all_latest(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[DriftReport, ...]:
        """Return the most recent drift report for each entity.

        Optimised query for per-entity latest across all entities.
        Returns one report per distinct entity.

        Args:
            limit: Maximum entities to return (default 100).

        Returns:
            Latest drift report per entity, ordered by divergence descending.
        """
        ...
