"""SQLite repository implementation for CodebaseStructureMap."""

import json
import sqlite3
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import ValidationError

from synthorg.core.codebase_structure_map import CodebaseStructureMap
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_CODEBASE_STRUCTURE_MAP_DELETE_FAILED,
    PERSISTENCE_CODEBASE_STRUCTURE_MAP_DESERIALIZE_FAILED,
    PERSISTENCE_CODEBASE_STRUCTURE_MAP_FETCH_FAILED,
    PERSISTENCE_CODEBASE_STRUCTURE_MAP_FETCHED,
    PERSISTENCE_CODEBASE_STRUCTURE_MAP_LIST_FAILED,
    PERSISTENCE_CODEBASE_STRUCTURE_MAP_LISTED,
    PERSISTENCE_CODEBASE_STRUCTURE_MAP_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import coerce_row_timestamp, format_iso_utc
from synthorg.persistence._shared.pagination import validate_pagination_args

if TYPE_CHECKING:
    from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000
_JSON_COLUMNS: tuple[str, ...] = (
    "modules",
    "entry_points",
    "test_suites",
    "build_files",
    "dependencies",
)


def _row_to_map(row: aiosqlite.Row) -> CodebaseStructureMap:
    """Reconstruct a ``CodebaseStructureMap`` from a database row.

    Returns:
        Result of type ``CodebaseStructureMap``.
    """
    data = dict(row)
    for column in _JSON_COLUMNS:
        data[column] = json.loads(data[column])
    data["scanned_at"] = coerce_row_timestamp(data["scanned_at"])
    return CodebaseStructureMap.model_validate(data)


class SQLiteCodebaseStructureMapRepository:
    """SQLite-backed codebase structure-map repository.

    Args:
        db: An open aiosqlite connection with ``row_factory`` set to
            ``aiosqlite.Row``.
        write_context: Async context manager that serializes writes on
            the shared connection.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    @staticmethod
    def _row_params(entity: CodebaseStructureMap) -> tuple[object, ...]:
        """Row params.

        Returns:
            Tuple of scalar SQL parameter values for INSERT/UPDATE.
        """
        return (
            entity.project_id,
            entity.source_ref,
            json.dumps([m.model_dump(mode="json") for m in entity.modules]),
            json.dumps([e.model_dump(mode="json") for e in entity.entry_points]),
            json.dumps([t.model_dump(mode="json") for t in entity.test_suites]),
            json.dumps([b.model_dump(mode="json") for b in entity.build_files]),
            json.dumps([d.model_dump(mode="json") for d in entity.dependencies]),
            format_iso_utc(entity.scanned_at),
            entity.content_hash,
        )

    async def _safe_rollback(self, *, event: str) -> None:
        """Best-effort rollback on the shared connection."""
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
            logger.warning(
                event,
                error_type=type(rollback_exc).__name__,
                error=safe_error_description(rollback_exc),
                rollback_failed=True,
            )

    async def save(self, entity: CodebaseStructureMap) -> None:
        """Persist a structure map via upsert (insert or update).

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(
                    """\
INSERT INTO codebase_structure_maps (project_id, source_ref, modules,
                                     entry_points, test_suites, build_files,
                                     dependencies, scanned_at, content_hash)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(project_id) DO UPDATE SET
    source_ref=excluded.source_ref,
    modules=excluded.modules,
    entry_points=excluded.entry_points,
    test_suites=excluded.test_suites,
    build_files=excluded.build_files,
    dependencies=excluded.dependencies,
    scanned_at=excluded.scanned_at,
    content_hash=excluded.content_hash""",
                    self._row_params(entity),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(
                    event=PERSISTENCE_CODEBASE_STRUCTURE_MAP_SAVE_FAILED
                )
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
            cursor = await self._db.execute(
                "SELECT * FROM codebase_structure_maps WHERE project_id = ?",
                (entity_id,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            cursor = await self._db.execute(
                "SELECT * FROM codebase_structure_maps "
                "ORDER BY project_id LIMIT ? OFFSET ?",
                (effective_limit, offset),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM codebase_structure_maps WHERE project_id = ?",
                    (entity_id,),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(
                    event=PERSISTENCE_CODEBASE_STRUCTURE_MAP_DELETE_FAILED
                )
                msg = f"Failed to delete structure map {entity_id!r}"
                logger.warning(
                    PERSISTENCE_CODEBASE_STRUCTURE_MAP_DELETE_FAILED,
                    project_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return cursor.rowcount > 0
