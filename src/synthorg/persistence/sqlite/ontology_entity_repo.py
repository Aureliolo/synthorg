"""SQLite-backed ontology entity repository."""

import contextlib
import json
import sqlite3
from collections.abc import Iterable

import aiosqlite

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.ontology import (
    ONTOLOGY_ENTITY_DESERIALIZATION_FAILED,
    ONTOLOGY_ENTITY_DUPLICATE,
    ONTOLOGY_ENTITY_NOT_FOUND,
    ONTOLOGY_SEARCH_EXECUTED,
)
from synthorg.ontology.errors import (
    OntologyDuplicateError,
    OntologyError,
    OntologyNotFoundError,
)
from synthorg.ontology.models import (
    EntityDefinition,
    EntityField,
    EntityRelation,
    EntitySource,
    EntityTier,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    DEFAULT_LIST_LIMIT,
    validate_pagination_args,
)
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)


class SQLiteOntologyEntityRepository:
    """SQLite implementation of ``OntologyEntityRepository``.

    Args:
        db: Open aiosqlite connection with ``row_factory`` set.
        write_context: Async context manager that serializes writes on
            the shared connection. Supplied by
            ``SQLitePersistenceBackend.write_context`` in production;
            tests can pass
            ``tests._shared.persistence.make_private_write_context()``
            for standalone construction.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier.

        Returns:
            Result of type ``NotBlankStr``.
        """
        return NotBlankStr("sqlite")

    def _row_to_entity(self, row: aiosqlite.Row) -> EntityDefinition:
        """Deserialize a database row into an EntityDefinition.

        Returns:
            Result of type ``EntityDefinition``.

        Raises:
            OntologyError: If the underlying call raises.
        """
        entity_name = row["name"]
        try:
            return EntityDefinition(
                name=entity_name,
                tier=EntityTier(row["tier"]),
                source=EntitySource(row["source"]),
                definition=row["definition"],
                fields=tuple(EntityField(**f) for f in json.loads(row["fields"])),
                constraints=tuple(json.loads(row["constraints"])),
                disambiguation=row["disambiguation"],
                relationships=tuple(
                    EntityRelation(**r) for r in json.loads(row["relationships"])
                ),
                created_by=row["created_by"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            msg = f"Corrupted entity definition for '{entity_name}'"
            logger.warning(
                ONTOLOGY_ENTITY_DESERIALIZATION_FAILED,
                entity_name=entity_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise OntologyError(msg) from exc

    def _entity_to_params(self, entity: EntityDefinition) -> dict[str, str]:
        """Serialize an EntityDefinition into SQL parameters.

        Returns:
            Result of type ``dict[str, str]``.
        """
        return {
            "name": entity.name,
            "tier": entity.tier.value,
            "source": entity.source.value,
            "definition": entity.definition,
            "fields": json.dumps(
                [f.model_dump(mode="json") for f in entity.fields],
            ),
            "constraints": json.dumps(list(entity.constraints)),
            "disambiguation": entity.disambiguation,
            "relationships": json.dumps(
                [r.model_dump(mode="json") for r in entity.relationships],
            ),
            "created_by": entity.created_by,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
        }

    async def register(self, entity: EntityDefinition) -> None:
        """Register a new entity definition.

        Raises:
            OntologyDuplicateError: If the underlying call raises.
            OntologyError: If the underlying call raises.
        """
        params = self._entity_to_params(entity)
        async with self._write_context():
            try:
                await self._db.execute(
                    """INSERT INTO entity_definitions
                       (name, tier, source, definition, fields, constraints,
                        disambiguation, relationships, created_by,
                        created_at, updated_at)
                       VALUES (:name, :tier, :source, :definition, :fields,
                               :constraints, :disambiguation, :relationships,
                               :created_by, :created_at, :updated_at)""",
                    params,
                )
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Entity '{entity.name}' already exists"
                logger.warning(
                    ONTOLOGY_ENTITY_DUPLICATE,
                    entity_name=entity.name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise OntologyDuplicateError(msg) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                # Other DB-layer failures (locked, IO error, ...) must
                # not silently escape; rollback + log + translate to
                # OntologyError so callers see a domain-typed failure.
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to register entity '{entity.name}'"
                logger.warning(
                    ONTOLOGY_ENTITY_DESERIALIZATION_FAILED,
                    entity_name=entity.name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise OntologyError(msg) from exc
        # Mutation-audit logging belongs in the service layer, not in
        # repositories.  Keeping ``logger.info(ONTOLOGY_ENTITY_REGISTERED)``
        # here would duplicate the audit trail every time multiple
        # callers share the repo (per CLAUDE.md persistence-boundary).

    async def save(self, entity: EntityDefinition) -> None:
        """Upsert an entity definition by name.

        Satisfies the generic ``IdKeyedRepository`` upsert contract:
        an existing entity is updated rather than raising
        ``OntologyDuplicateError`` (which ``register`` does for the
        insert-only path).

        A single ``INSERT ... ON CONFLICT(name) DO UPDATE`` is used so
        the existence check and the write are one atomic statement;
        a ``get``-then-``register``/``update`` sequence races with a
        concurrent save on the same name. ``created_by`` / ``created_at``
        are intentionally left untouched on conflict so the original
        creator and creation time survive an upsert.

        Raises:
            OntologyError: If the underlying call raises.
        """
        params = self._entity_to_params(entity)
        async with self._write_context():
            try:
                await self._db.execute(
                    """INSERT INTO entity_definitions
                       (name, tier, source, definition, fields, constraints,
                        disambiguation, relationships, created_by,
                        created_at, updated_at)
                       VALUES (:name, :tier, :source, :definition, :fields,
                               :constraints, :disambiguation, :relationships,
                               :created_by, :created_at, :updated_at)
                       ON CONFLICT(name) DO UPDATE SET
                           tier = excluded.tier,
                           source = excluded.source,
                           definition = excluded.definition,
                           fields = excluded.fields,
                           constraints = excluded.constraints,
                           disambiguation = excluded.disambiguation,
                           relationships = excluded.relationships,
                           updated_at = excluded.updated_at""",
                    params,
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to save entity '{entity.name}'"
                logger.warning(
                    ONTOLOGY_ENTITY_DESERIALIZATION_FAILED,
                    entity_name=entity.name,
                    op="save",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise OntologyError(msg) from exc

    async def get(self, name: str) -> EntityDefinition | None:
        """Retrieve an entity definition by name, or None if not found.

        Returns:
            The matching entity, or ``None`` when no row matches.
        """
        async with self._db.execute(
            "SELECT * FROM entity_definitions WHERE name = :name",
            {"name": name},
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_entity(row)

    async def update(self, entity: EntityDefinition) -> None:
        """Update an existing entity definition.

        Raises:
            OntologyNotFoundError: If the underlying call raises.
            OntologyError: If the underlying call raises.
        """
        params = self._entity_to_params(entity)
        async with self._write_context():
            try:
                async with self._db.execute(
                    """UPDATE entity_definitions
                       SET tier = :tier, source = :source,
                           definition = :definition, fields = :fields,
                           constraints = :constraints,
                           disambiguation = :disambiguation,
                           relationships = :relationships,
                           updated_at = :updated_at
                       WHERE name = :name""",
                    params,
                ) as cursor:
                    if cursor.rowcount == 0:
                        # Roll back so the empty UPDATE does not leave the
                        # shared connection inside an open implicit
                        # transaction.
                        with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                            await self._db.rollback()
                        msg = f"Entity '{entity.name}' not found"
                        logger.warning(
                            ONTOLOGY_ENTITY_NOT_FOUND,
                            entity_name=entity.name,
                            op="update",
                        )
                        raise OntologyNotFoundError(msg)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to update entity '{entity.name}'"
                logger.warning(
                    ONTOLOGY_ENTITY_DESERIALIZATION_FAILED,
                    entity_name=entity.name,
                    op="update",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise OntologyError(msg) from exc

    async def delete(self, name: str) -> bool:
        """Delete an entity definition by name.

        Returns ``True`` iff a row existed (generic IdKeyedRepository contract).

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            OntologyError: If the underlying call raises.
        """
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM entity_definitions WHERE name = :name",
                    {"name": name},
                ) as cursor:
                    existed = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to delete entity '{name}'"
                logger.warning(
                    ONTOLOGY_ENTITY_DESERIALIZATION_FAILED,
                    entity_name=name,
                    op="delete",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise OntologyError(msg) from exc
            else:
                return existed

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[EntityDefinition, ...]:
        """List all entity definitions in name order.

        Returns:
            The matching entities.
        """
        limit = validate_pagination_args(
            limit, offset, event=ONTOLOGY_ENTITY_DESERIALIZATION_FAILED
        )
        async with self._db.execute(
            """SELECT * FROM entity_definitions
               ORDER BY name ASC
               LIMIT :limit OFFSET :offset""",
            {"limit": limit, "offset": offset},
        ) as cursor:
            rows = await cursor.fetchall()
        return self._rows_to_entities(rows)

    async def list_entities(
        self,
        *,
        tier: EntityTier | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[EntityDefinition, ...]:
        """List entities, optionally filtered by tier and paginated.

        The legacy hard cap of 1000 rows applies when *limit* is
        ``None`` so callers that haven't migrated to pagination still
        receive a bounded result set.

        Returns:
            The matching entities.
        """
        effective_limit = 1000 if limit is None else int(limit)
        effective_offset = max(0, int(offset))
        sql: str
        params: dict[str, object]
        if tier is not None:
            sql = """SELECT * FROM entity_definitions
                   WHERE tier = :tier
                   ORDER BY name ASC
                   LIMIT :limit OFFSET :offset"""
            params = {
                "tier": tier.value,
                "limit": effective_limit,
                "offset": effective_offset,
            }
        else:
            sql = """SELECT * FROM entity_definitions
                   ORDER BY name ASC
                   LIMIT :limit OFFSET :offset"""
            params = {"limit": effective_limit, "offset": effective_offset}
        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return self._rows_to_entities(rows)

    async def search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[EntityDefinition, ...]:
        """Search entities by name or definition text.

        Returns:
            The matching collection.
        """
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        effective_limit = 1000 if limit is None else int(limit)
        effective_offset = max(0, int(offset))
        async with self._db.execute(
            """SELECT * FROM entity_definitions
               WHERE name LIKE :pattern ESCAPE '\\'
                  OR definition LIKE :pattern ESCAPE '\\'
               ORDER BY name ASC
               LIMIT :limit OFFSET :offset""",
            {
                "pattern": pattern,
                "limit": effective_limit,
                "offset": effective_offset,
            },
        ) as cursor:
            rows = list(await cursor.fetchall())
        logger.debug(
            ONTOLOGY_SEARCH_EXECUTED,
            query=query,
            result_count=len(rows),
        )
        return self._rows_to_entities(rows)

    def _rows_to_entities(
        self,
        rows: Iterable[aiosqlite.Row],
    ) -> tuple[EntityDefinition, ...]:
        """Deserialize rows, skipping corrupted entries.

        Returns:
            The matching collection.
        """
        results: list[EntityDefinition] = []
        for row in rows:
            try:
                results.append(self._row_to_entity(row))
            except OntologyError:
                continue
        return tuple(results)

    async def get_version_manifest(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> dict[NotBlankStr, int]:
        """Return a bounded page of the latest version per entity.

        Entities page in ``entity_id`` order so a cursor walk is
        stable; callers needing the whole manifest drain via
        :func:`synthorg.persistence._shared.collect_all_mapping`.

        Returns:
            Mapping of ``entity_id`` to its current ``schema_version``; empty when no
            entities are registered.
        """
        limit = validate_pagination_args(
            limit, offset, event=ONTOLOGY_ENTITY_DESERIALIZATION_FAILED
        )
        async with self._db.execute(
            """SELECT entity_id, MAX(version) AS latest_version
               FROM entity_definition_versions
               GROUP BY entity_id
               ORDER BY entity_id
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
        return {NotBlankStr(row["entity_id"]): row["latest_version"] for row in rows}
