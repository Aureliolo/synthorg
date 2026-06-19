"""Ontology service -- orchestrates backend, versioning, and bootstrap."""

from datetime import UTC, datetime
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.pagination import collect_all_mapping
from synthorg.observability import get_logger
from synthorg.observability.events.ontology import (
    ONTOLOGY_BOOTSTRAP_COMPLETED,
    ONTOLOGY_BOOTSTRAP_ENTITY_SKIPPED,
    ONTOLOGY_CONFIG_LOADED,
    ONTOLOGY_ENTITY_NOT_FOUND,
    ONTOLOGY_VERSION_SNAPSHOT,
)
from synthorg.ontology.config import EntitiesConfig, OntologyConfig
from synthorg.ontology.decorator import get_entity_registry
from synthorg.ontology.errors import OntologyDuplicateError, OntologyNotFoundError
from synthorg.ontology.models import (
    EntityDefinition,
    EntityField,
    EntitySource,
    EntityTier,
)
from synthorg.persistence.ontology_protocol import OntologyEntityRepository
from synthorg.versioning.models import VersionSnapshot
from synthorg.versioning.service import VersioningService

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


class OntologyService:
    """Orchestrates the ontology backend, versioning, and bootstrap.

    Args:
        backend: The ontology storage backend.
        versioning: Versioning service for entity definition snapshots.
        config: Ontology configuration.
    """

    def __init__(
        self,
        backend: OntologyEntityRepository,
        versioning: VersioningService[EntityDefinition],
        config: OntologyConfig,
    ) -> None:
        self._backend = backend
        self._versioning = versioning
        self._config = config

    # ── Bootstrap ───────────────────────────────────────────────

    async def bootstrap(self) -> int:
        """Register all ``@ontology_entity``-decorated models.

        Discovers entity definitions from the decorator registry and
        registers each in the backend.  Already-registered entities
        are skipped (idempotent).

        Returns:
            Number of newly registered entities.
        """
        registry = get_entity_registry()
        registered = 0
        for name, entity in registry.items():
            try:
                await self._backend.register(entity)
                registered += 1
            except OntologyDuplicateError:
                logger.debug(
                    ONTOLOGY_BOOTSTRAP_ENTITY_SKIPPED,
                    entity_name=name,
                    reason="already registered",
                )
                continue
            try:
                await self._snapshot(entity)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    ONTOLOGY_BOOTSTRAP_ENTITY_SKIPPED,
                    entity_name=name,
                    reason="version snapshot failed",
                )
        logger.info(
            ONTOLOGY_BOOTSTRAP_COMPLETED,
            total=len(registry),
            registered=registered,
            skipped=len(registry) - registered,
        )
        return registered

    async def bootstrap_from_config(
        self,
        entities_config: EntitiesConfig,
    ) -> int:
        """Register user-defined entities from YAML configuration.

        Already-registered entities are skipped (idempotent).

        Args:
            entities_config: Parsed entity entries from YAML.

        Returns:
            Number of newly registered entities.
        """
        registered = 0
        now = datetime.now(UTC)
        for entry in entities_config.entries:
            fields = tuple(
                EntityField(
                    name=field_name,
                    type_hint="str",
                    description=desc,
                )
                for field_name, desc in entry.fields.items()
            )
            entity = EntityDefinition(
                name=entry.name,
                tier=EntityTier.USER,
                source=EntitySource.CONFIG,
                definition=entry.definition,
                fields=fields,
                constraints=entry.constraints,
                disambiguation=entry.disambiguation,
                created_by="config",
                created_at=now,
                updated_at=now,
            )
            try:
                await self._backend.register(entity)
                registered += 1
            except OntologyDuplicateError:
                logger.debug(
                    ONTOLOGY_BOOTSTRAP_ENTITY_SKIPPED,
                    entity_name=entry.name,
                    reason="already registered",
                )
                continue
            try:
                await self._snapshot(entity)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    ONTOLOGY_BOOTSTRAP_ENTITY_SKIPPED,
                    entity_name=entry.name,
                    reason="version snapshot failed",
                )
        logger.info(
            ONTOLOGY_CONFIG_LOADED,
            total=len(entities_config.entries),
            registered=registered,
        )
        return registered

    # ── CRUD ────────────────────────────────────────────────────

    async def register(self, entity: EntityDefinition) -> None:
        """Register a new entity definition and snapshot it.

        Args:
            entity: The entity definition to register.

        Raises:
            OntologyDuplicateError: If the entity already exists.
        """
        await self._backend.register(entity)
        await self._snapshot(entity)

    async def update(self, entity: EntityDefinition) -> None:
        """Update an existing entity definition and snapshot it.

        Args:
            entity: The updated entity definition.

        Raises:
            OntologyNotFoundError: If the entity does not exist.
        """
        await self._backend.update(entity)
        await self._snapshot(entity)

    async def delete(self, name: str) -> None:
        """Delete an entity definition.

        Args:
            name: Entity name.

        Raises:
            OntologyNotFoundError: If the entity does not exist.
        """
        await self._backend.delete(name)

    async def get(self, name: str) -> EntityDefinition:
        """Retrieve an entity definition by name.

        Args:
            name: Entity name.

        Returns:
            The matching entity definition.

        Raises:
            OntologyNotFoundError: If not found.
        """
        entity = await self._backend.get(name)
        if entity is None:
            msg = f"Entity '{name}' not found"
            logger.warning(
                ONTOLOGY_ENTITY_NOT_FOUND,
                entity_name=name,
                error_type=OntologyNotFoundError.__name__,
            )
            raise OntologyNotFoundError(msg)
        return entity

    async def list_entities(
        self,
        *,
        tier: EntityTier | None = None,
    ) -> tuple[EntityDefinition, ...]:
        """List entity definitions, optionally filtered by tier.

        Args:
            tier: Optional tier filter.

        Returns:
            Tuple of matching entity definitions.
        """
        return await self._backend.list_entities(tier=tier)

    async def search(self, query: str) -> tuple[EntityDefinition, ...]:
        """Search entity definitions by name or definition text.

        Args:
            query: Search string.

        Returns:
            Tuple of matching entity definitions.
        """
        return await self._backend.search(query)

    async def get_version_manifest(self) -> dict[str, int]:
        """Return the latest version for each entity.

        Drains every bounded backend page so callers (drift
        detection, the delegation entity guard, the API endpoint)
        keep receiving the complete manifest; a truncated manifest
        would make drift detection miss entities.

        Returns:
            Mapping from entity name to latest version number.
        """
        return await collect_all_mapping(
            lambda limit, offset: self._backend.get_version_manifest(
                limit=limit,
                offset=offset,
            ),
        )

    async def list_versions(
        self,
        entity_name: str,
        *,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> tuple[VersionSnapshot[EntityDefinition], ...]:
        """List version snapshots for an entity.

        Args:
            entity_name: Entity to query versions for.
            limit: Maximum versions to return.
            offset: Number of versions to skip.

        Returns:
            Version snapshots ordered newest first.
        """
        return await self._versioning._repo.list_versions(  # noqa: SLF001
            entity_name,
            limit=limit,
            offset=offset,
        )

    async def get_version(
        self,
        entity_name: str,
        version: int,
    ) -> VersionSnapshot[EntityDefinition] | None:
        """Get a specific version snapshot.

        Args:
            entity_name: Entity name.
            version: Version number to retrieve.

        Returns:
            The version snapshot, or None if not found.
        """
        return await self._versioning._repo.get_version(  # noqa: SLF001
            entity_name,
            version,
        )

    # ── Internal ────────────────────────────────────────────────

    async def _snapshot(self, entity: EntityDefinition) -> None:
        """Create a version snapshot if content changed."""
        saved_by = entity.created_by
        result = await self._versioning.snapshot_if_changed(
            entity_id=entity.name,
            snapshot=entity,
            saved_by=saved_by,
        )
        if result is not None:
            logger.debug(
                ONTOLOGY_VERSION_SNAPSHOT,
                entity_name=entity.name,
                version=result.version,
            )
