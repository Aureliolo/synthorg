"""Postgres repository implementation for CodebaseStructureMap."""

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from synthorg.core.codebase_structure_map import CodebaseStructureMap
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.codebase_structure_map import (
    PERSISTENCE_CODEBASE_STRUCTURE_MAP_DELETE_FAILED,
    PERSISTENCE_CODEBASE_STRUCTURE_MAP_DESERIALIZE_FAILED,
    PERSISTENCE_CODEBASE_STRUCTURE_MAP_FETCH_FAILED,
    PERSISTENCE_CODEBASE_STRUCTURE_MAP_FETCHED,
    PERSISTENCE_CODEBASE_STRUCTURE_MAP_LIST_FAILED,
    PERSISTENCE_CODEBASE_STRUCTURE_MAP_LISTED,
    PERSISTENCE_CODEBASE_STRUCTURE_MAP_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import coerce_row_timestamp
from synthorg.persistence._shared.pagination import validate_pagination_args

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000


def _row_to_map(row: DictRow) -> CodebaseStructureMap:
    """Reconstruct a ``CodebaseStructureMap`` from a Postgres dict_row.

    JSONB columns are decoded to Python lists by psycopg, so they pass
    straight to ``model_validate``.

    Returns:
        Result of type ``CodebaseStructureMap``.
    """
    data = dict(row)
    data["scanned_at"] = coerce_row_timestamp(data["scanned_at"])
    return CodebaseStructureMap.model_validate(data)


class PostgresCodebaseStructureMapRepository:
    """Postgres-backed codebase structure-map repository.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @staticmethod
    def _row_params(entity: CodebaseStructureMap) -> tuple[object, ...]:
        """Row params.

        Returns:
            Tuple of scalar SQL parameter values for INSERT/UPDATE.
        """
        return (
            entity.project_id,
            entity.source_ref,
            Jsonb([m.model_dump(mode="json") for m in entity.modules]),
            Jsonb([e.model_dump(mode="json") for e in entity.entry_points]),
            Jsonb([t.model_dump(mode="json") for t in entity.test_suites]),
            Jsonb([b.model_dump(mode="json") for b in entity.build_files]),
            Jsonb([d.model_dump(mode="json") for d in entity.dependencies]),
            entity.scanned_at,
            entity.content_hash,
        )

    async def save(self, entity: CodebaseStructureMap) -> None:
        """Persist a structure map via upsert (insert or update).

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO codebase_structure_maps (project_id, source_ref,
                        modules, entry_points, test_suites, build_files,
                        dependencies, scanned_at, content_hash)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(project_id) DO UPDATE SET
                        source_ref=EXCLUDED.source_ref,
                        modules=EXCLUDED.modules,
                        entry_points=EXCLUDED.entry_points,
                        test_suites=EXCLUDED.test_suites,
                        build_files=EXCLUDED.build_files,
                        dependencies=EXCLUDED.dependencies,
                        scanned_at=EXCLUDED.scanned_at,
                        content_hash=EXCLUDED.content_hash
                    """,
                    self._row_params(entity),
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save structure map {entity.project_id!r}"
            logger.warning(
                PERSISTENCE_CODEBASE_STRUCTURE_MAP_SAVE_FAILED,
                project_id=entity.project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> CodebaseStructureMap | None:
        """Retrieve a structure map by owning project id.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM codebase_structure_maps WHERE project_id = %s",
                    (entity_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch structure map {entity_id!r}"
            logger.warning(
                PERSISTENCE_CODEBASE_STRUCTURE_MAP_FETCH_FAILED,
                project_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(
                PERSISTENCE_CODEBASE_STRUCTURE_MAP_FETCHED,
                project_id=entity_id,
                found=False,
            )
            return None
        try:
            structure_map = _row_to_map(row)
        except (ValueError, ValidationError, KeyError) as exc:
            msg = f"Failed to deserialize structure map {entity_id!r}"
            logger.warning(
                PERSISTENCE_CODEBASE_STRUCTURE_MAP_DESERIALIZE_FAILED,
                project_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_CODEBASE_STRUCTURE_MAP_FETCHED,
            project_id=entity_id,
            found=True,
        )
        return structure_map

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CodebaseStructureMap, ...]:
        """List structure maps in project-id order.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CODEBASE_STRUCTURE_MAP_LIST_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM codebase_structure_maps "
                    "ORDER BY project_id LIMIT %s OFFSET %s",
                    (effective_limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list structure maps"
            logger.warning(
                PERSISTENCE_CODEBASE_STRUCTURE_MAP_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            maps = tuple(_row_to_map(row) for row in rows)
        except (ValueError, ValidationError, KeyError) as exc:
            msg = "Failed to deserialize structure maps"
            logger.warning(
                PERSISTENCE_CODEBASE_STRUCTURE_MAP_DESERIALIZE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_CODEBASE_STRUCTURE_MAP_LISTED, count=len(maps))
        return maps

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a structure map by owning project id.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM codebase_structure_maps WHERE project_id = %s",
                    (entity_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete structure map {entity_id!r}"
            logger.warning(
                PERSISTENCE_CODEBASE_STRUCTURE_MAP_DELETE_FAILED,
                project_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted
