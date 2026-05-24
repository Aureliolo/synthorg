"""Postgres-backed ontology entity repository."""

import json
from collections.abc import Iterable  # noqa: TC003
from typing import TYPE_CHECKING, Any

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.ontology import (
    ONTOLOGY_ENTITY_DESERIALIZATION_FAILED,
    ONTOLOGY_ENTITY_DUPLICATE,
    ONTOLOGY_ENTITY_NOT_FOUND,
    ONTOLOGY_ENTITY_REGISTERED,
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

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


def _import_dict_row() -> Any:
    """Lazily resolve ``psycopg.rows.dict_row``.

    Returns:
        Result of type ``Any``.
    """
    from psycopg.rows import dict_row  # noqa: PLC0415

    return dict_row


def _import_integrity_error() -> type[BaseException]:
    """Lazily resolve ``psycopg.errors.UniqueViolation``.

    Returns:
        Result of type ``type[BaseException]``.
    """
    from psycopg.errors import UniqueViolation  # noqa: PLC0415

    return UniqueViolation


logger = get_logger(__name__)


class PostgresOntologyEntityRepository:
    """Postgres implementation of ``OntologyEntityRepository``."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool
        self._dict_row = _import_dict_row()
        self._unique_violation = _import_integrity_error()

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier.

        Returns:
            Result of type ``NotBlankStr``.
        """
        return NotBlankStr("postgres")

    def _row_to_entity(self, row: dict[str, Any]) -> EntityDefinition:
        """Deserialize a psycopg dict row into an EntityDefinition.

        Postgres JSONB columns come back as already-parsed lists/dicts
        when psycopg has the jsonb loader wired in; callers that store
        values through ``json.dumps`` then fetch via raw text adapters
        get a ``str`` back instead.  Handle both so the repo stays
        portable across psycopg loader configurations.

        Returns:
            Result of type ``EntityDefinition``.

        Raises:
            OntologyError: If the underlying call raises.
        """
        entity_name = row["name"]
        try:
            fields_raw = row["fields"]
            constraints_raw = row["constraints"]
            relationships_raw = row["relationships"]
            fields_data = (
                json.loads(fields_raw) if isinstance(fields_raw, str) else fields_raw
            )
            constraints_data = (
                json.loads(constraints_raw)
                if isinstance(constraints_raw, str)
                else constraints_raw
            )
            relationships_data = (
                json.loads(relationships_raw)
                if isinstance(relationships_raw, str)
                else relationships_raw
            )
            return EntityDefinition(
                name=entity_name,
                tier=EntityTier(row["tier"]),
                source=EntitySource(row["source"]),
                definition=row["definition"],
                fields=tuple(EntityField(**f) for f in fields_data),
                constraints=tuple(constraints_data),
                disambiguation=row["disambiguation"],
                relationships=tuple(EntityRelation(**r) for r in relationships_data),
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
        """
        params = self._entity_to_params(entity)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """INSERT INTO entity_definitions
                       (name, tier, source, definition, fields, constraints,
                        disambiguation, relationships, created_by,
                        created_at, updated_at)
                       VALUES (%(name)s, %(tier)s, %(source)s, %(definition)s,
                               %(fields)s, %(constraints)s, %(disambiguation)s,
                               %(relationships)s, %(created_by)s,
                               %(created_at)s, %(updated_at)s)""",
                    params,
                )
        except self._unique_violation as exc:
            msg = f"Entity '{entity.name}' already exists"
            logger.warning(
                ONTOLOGY_ENTITY_DUPLICATE,
                entity_name=entity.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise OntologyDuplicateError(msg) from exc
        logger.info(
            ONTOLOGY_ENTITY_REGISTERED,
            entity_name=entity.name,
            tier=entity.tier.value,
        )

    async def save(self, entity: EntityDefinition) -> None:
        """Upsert an entity definition by name.

        Satisfies the generic ``IdKeyedRepository`` upsert contract:
        an existing entity is updated rather than raising
        ``OntologyDuplicateError`` (which ``register`` does for the
        insert-only path).

        A single ``INSERT ... ON CONFLICT (name) DO UPDATE`` is used so
        the existence check and the write are one atomic statement;
        a ``get``-then-``register``/``update`` sequence races with a
        concurrent save on the same name. ``created_by`` / ``created_at``
        are intentionally left untouched on conflict so the original
        creator and creation time survive an upsert.
        """
        params = self._entity_to_params(entity)
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO entity_definitions
                   (name, tier, source, definition, fields, constraints,
                    disambiguation, relationships, created_by,
                    created_at, updated_at)
                   VALUES (%(name)s, %(tier)s, %(source)s, %(definition)s,
                           %(fields)s, %(constraints)s, %(disambiguation)s,
                           %(relationships)s, %(created_by)s,
                           %(created_at)s, %(updated_at)s)
                   ON CONFLICT (name) DO UPDATE SET
                       tier = EXCLUDED.tier,
                       source = EXCLUDED.source,
                       definition = EXCLUDED.definition,
                       fields = EXCLUDED.fields,
                       constraints = EXCLUDED.constraints,
                       disambiguation = EXCLUDED.disambiguation,
                       relationships = EXCLUDED.relationships,
                       updated_at = EXCLUDED.updated_at""",
                params,
            )

    async def get(self, name: str) -> EntityDefinition | None:
        """Retrieve an entity definition by name, or None if not found.

        Returns:
            The matching entity, or ``None`` when no row matches.
        """
        dict_row = self._dict_row
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                "SELECT * FROM entity_definitions WHERE name = %(name)s",
                {"name": name},
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return self._row_to_entity(row)

    async def update(self, entity: EntityDefinition) -> None:
        """Update an existing entity definition.

        Raises:
            OntologyNotFoundError: If the underlying call raises.
        """
        params = self._entity_to_params(entity)
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """UPDATE entity_definitions
                   SET tier = %(tier)s, source = %(source)s,
                       definition = %(definition)s, fields = %(fields)s,
                       constraints = %(constraints)s,
                       disambiguation = %(disambiguation)s,
                       relationships = %(relationships)s,
                       updated_at = %(updated_at)s
                   WHERE name = %(name)s""",
                params,
            )
            if cur.rowcount == 0:
                msg = f"Entity '{entity.name}' not found"
                logger.warning(
                    ONTOLOGY_ENTITY_NOT_FOUND,
                    entity_name=entity.name,
                    op="update",
                )
                raise OntologyNotFoundError(msg)

    async def delete(self, name: str) -> bool:
        """Delete an entity definition by name.

        Returns ``True`` iff a row existed (generic IdKeyedRepository contract).

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.
        """
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM entity_definitions WHERE name = %(name)s",
                {"name": name},
            )
            return cur.rowcount > 0

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
        dict_row = self._dict_row
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                "SELECT * FROM entity_definitions "
                "ORDER BY name ASC "
                "LIMIT %(limit)s OFFSET %(offset)s",
                {"limit": limit, "offset": offset},
            )
            rows = await cur.fetchall()
        return self._rows_to_entities(rows)

    async def list_entities(
        self,
        *,
        tier: EntityTier | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[EntityDefinition, ...]:
        """List entities, optionally filtered by tier and paginated.

        Returns:
            The matching entities.
        """
        effective_limit = 1000 if limit is None else int(limit)
        effective_offset = max(0, int(offset))
        dict_row = self._dict_row
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            if tier is not None:
                await cur.execute(
                    """SELECT * FROM entity_definitions
                       WHERE tier = %(tier)s
                       ORDER BY name ASC
                       LIMIT %(limit)s OFFSET %(offset)s""",
                    {
                        "tier": tier.value,
                        "limit": effective_limit,
                        "offset": effective_offset,
                    },
                )
            else:
                await cur.execute(
                    "SELECT * FROM entity_definitions "
                    "ORDER BY name ASC "
                    "LIMIT %(limit)s OFFSET %(offset)s",
                    {"limit": effective_limit, "offset": effective_offset},
                )
            rows = await cur.fetchall()
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
        dict_row = self._dict_row
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                """SELECT * FROM entity_definitions
                   WHERE name LIKE %(pattern)s ESCAPE '\\'
                      OR definition LIKE %(pattern)s ESCAPE '\\'
                   ORDER BY name ASC
                   LIMIT %(limit)s OFFSET %(offset)s""",
                {
                    "pattern": pattern,
                    "limit": effective_limit,
                    "offset": effective_offset,
                },
            )
            rows = list(await cur.fetchall())
        logger.debug(
            ONTOLOGY_SEARCH_EXECUTED,
            query=query,
            result_count=len(rows),
        )
        return self._rows_to_entities(rows)

    def _rows_to_entities(
        self,
        rows: Iterable[dict[str, Any]],
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
            The matching entity, or ``None`` when no row matches.
        """
        limit = validate_pagination_args(
            limit, offset, event=ONTOLOGY_ENTITY_DESERIALIZATION_FAILED
        )
        dict_row = self._dict_row
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                """SELECT entity_id, MAX(version) AS latest_version
                   FROM entity_definition_versions
                   GROUP BY entity_id
                   ORDER BY entity_id
                   LIMIT %s OFFSET %s""",
                (limit, offset),
            )
            rows = await cur.fetchall()
        return {NotBlankStr(row["entity_id"]): row["latest_version"] for row in rows}
