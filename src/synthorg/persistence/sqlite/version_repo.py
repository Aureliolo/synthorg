"""Generic SQLite repository for versioned entity snapshots.

Parameterisable by entity type ``T`` via ``serialize_snapshot`` and
``deserialize_snapshot`` callables, so a single implementation covers
all versioned entities.  Each entity type uses a dedicated table;
the table name is validated and injected at construction time.

Example::

    from synthorg.core.agent import AgentIdentity

    repo: SQLiteVersionRepository[AgentIdentity] = SQLiteVersionRepository(
        db=db,
        table_name="agent_identity_versions",
        serialize_snapshot=lambda m: json.dumps(m.model_dump(mode="json")),
        deserialize_snapshot=lambda s: AgentIdentity.model_validate(json.loads(s)),
    )
"""

import json
import re
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import BaseModel, ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.versioning import (
    VERSION_COUNT_FAILED,
    VERSION_DELETE_FAILED,
    VERSION_FETCH_FAILED,
    VERSION_LIST_FAILED,
    VERSION_LISTED,
    VERSION_SAVE_FAILED,
)
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001
from synthorg.persistence.version_protocol import _DEFAULT_LIST_LIMIT_50
from synthorg.versioning.models import VersionSnapshot

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)

#: Allowed table name pattern -- lowercase letters, digits, underscores.
_TABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_SELECT_COLUMNS = "entity_id, version, content_hash, snapshot, saved_by, saved_at"


class SQLiteVersionRepository[T: BaseModel]:
    """SQLite-backed generic version snapshot repository.

    All SQL queries are built at construction time using the validated
    ``table_name`` parameter.  ``save_version`` uses ``INSERT OR IGNORE``
    for idempotency (safe for retries and concurrent writes).  Naive
    timestamps in ``saved_at`` are interpreted as UTC.

    Args:
        db: An open aiosqlite connection with ``row_factory`` set to
            ``aiosqlite.Row``.
        table_name: Name of the SQL table storing snapshots for this
            entity type.  Must match ``[a-z][a-z0-9_]*``.
        serialize_snapshot: Callable that converts a ``T`` instance to
            a JSON string for persistence.
        deserialize_snapshot: Callable that converts a stored JSON
            string back to a ``T`` instance.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        table_name: str,
        serialize_snapshot: Callable[[T], str],
        deserialize_snapshot: Callable[[str], T],
        write_context: WriteContext,
    ) -> None:
        if not _TABLE_NAME_RE.match(table_name):
            msg = f"Invalid table name: {table_name!r} (must match [a-z][a-z0-9_]*)"
            raise ValueError(msg)
        self._db = db
        self._table = table_name
        self._serialize = serialize_snapshot
        self._deserialize = deserialize_snapshot
        self._write_context = write_context
        _t = self._table
        _c = _SELECT_COLUMNS
        self._insert_sql = (
            f"INSERT OR IGNORE INTO {_t} "  # noqa: S608
            f"({_c}) VALUES (?, ?, ?, ?, ?, ?)"
        )
        self._select_one_sql = (
            f"SELECT {_c} FROM {_t} WHERE entity_id = ? AND version = ?"  # noqa: S608
        )
        self._select_latest_sql = (
            f"SELECT {_c} FROM {_t} "  # noqa: S608
            f"WHERE entity_id = ? ORDER BY version DESC LIMIT 1"
        )
        self._select_by_hash_sql = (
            f"SELECT {_c} FROM {_t} "  # noqa: S608
            f"WHERE entity_id = ? AND content_hash = ? "
            f"ORDER BY version DESC LIMIT 1"
        )
        self._select_list_sql = (
            f"SELECT {_c} FROM {_t} "  # noqa: S608
            f"WHERE entity_id = ? ORDER BY version DESC LIMIT ? OFFSET ?"
        )
        self._count_sql = f"SELECT COUNT(*) FROM {_t} WHERE entity_id = ?"  # noqa: S608
        self._delete_sql = f"DELETE FROM {_t} WHERE entity_id = ?"  # noqa: S608

    def _deserialize_row(self, row: aiosqlite.Row) -> VersionSnapshot[T]:
        """Reconstruct a VersionSnapshot from a database row."""
        data = dict(row)
        try:
            dt = datetime.fromisoformat(str(data["saved_at"]))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return VersionSnapshot(
                entity_id=data["entity_id"],
                version=int(data["version"]),
                content_hash=data["content_hash"],
                snapshot=self._deserialize(data["snapshot"]),
                saved_by=data["saved_by"],
                saved_at=dt,
            )
        except json.JSONDecodeError as exc:
            context = f"{data.get('entity_id', '?')}@v{data.get('version', '?')}"
            msg = f"Corrupt JSON in version snapshot {context!r}: {safe_error_description(exc)}"  # noqa: E501
            logger.warning(
                VERSION_FETCH_FAILED,
                table=self._table,
                context=context,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="json_corrupt",
            )
            raise QueryError(msg) from exc
        except ValidationError as exc:
            context = f"{data.get('entity_id', '?')}@v{data.get('version', '?')}"
            msg = f"Schema mismatch in version snapshot {context!r}: {safe_error_description(exc)}"  # noqa: E501
            logger.warning(
                VERSION_FETCH_FAILED,
                table=self._table,
                context=context,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="schema_drift",
            )
            raise QueryError(msg) from exc
        except (ValueError, KeyError) as exc:
            context = f"{data.get('entity_id', '?')}@v{data.get('version', '?')}"
            msg = f"Failed to deserialize version snapshot {context!r}: {safe_error_description(exc)}"  # noqa: E501
            logger.warning(
                VERSION_FETCH_FAILED,
                table=self._table,
                context=context,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="unexpected",
            )
            raise QueryError(msg) from exc
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            # Catch-all for unconstrained deserialize_snapshot callbacks
            # (e.g. TypeError, AttributeError) so all callback errors
            # are normalized to QueryError.
            context = f"{data.get('entity_id', '?')}@v{data.get('version', '?')}"
            msg = f"Failed to deserialize version snapshot {context!r}: {safe_error_description(exc)}"  # noqa: E501
            logger.warning(
                VERSION_FETCH_FAILED,
                table=self._table,
                context=context,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="callback_error",
            )
            raise QueryError(msg) from exc

    async def save_version(self, version: VersionSnapshot[T]) -> bool:
        """Persist a version snapshot (insert only, idempotent).

        Returns:
            ``True`` if the row was actually inserted; ``False`` if the
            ``(entity_id, version)`` pair already existed and the write
            was silently dropped by ``INSERT OR IGNORE``.

        Raises:
            QueryError: If serialization or database write fails.
        """
        try:
            serialized = self._serialize(version.snapshot)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            msg = (
                f"Failed to serialize snapshot for version "
                f"{version.version} of {version.entity_id!r} "
                f"in {self._table}"
            )
            logger.warning(
                VERSION_SAVE_FAILED,
                table=self._table,
                entity_id=version.entity_id,
                version=version.version,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    self._insert_sql,
                    (
                        version.entity_id,
                        version.version,
                        version.content_hash,
                        serialized,
                        version.saved_by,
                        version.saved_at.isoformat(),
                    ),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = (
                    f"Failed to save version {version.version} "
                    f"for {version.entity_id!r} in {self._table}"
                )
                logger.warning(
                    VERSION_SAVE_FAILED,
                    table=self._table,
                    entity_id=version.entity_id,
                    version=version.version,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0

    async def get_version(
        self,
        entity_id: NotBlankStr,
        version: int,
    ) -> VersionSnapshot[T] | None:
        """Retrieve a specific version snapshot."""
        try:
            cursor = await self._db.execute(
                self._select_one_sql,
                (entity_id, version),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch version {version} for {entity_id!r}"
            logger.warning(
                VERSION_FETCH_FAILED,
                table=self._table,
                entity_id=entity_id,
                version=version,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return self._deserialize_row(row)

    async def get_latest_version(
        self,
        entity_id: NotBlankStr,
    ) -> VersionSnapshot[T] | None:
        """Retrieve the most recent version snapshot for an entity."""
        try:
            cursor = await self._db.execute(
                self._select_latest_sql,
                (entity_id,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch latest version for {entity_id!r}"
            logger.warning(
                VERSION_FETCH_FAILED,
                table=self._table,
                entity_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return self._deserialize_row(row)

    async def get_by_content_hash(
        self,
        entity_id: NotBlankStr,
        content_hash: NotBlankStr,
    ) -> VersionSnapshot[T] | None:
        """Retrieve a version by its content hash."""
        try:
            cursor = await self._db.execute(
                self._select_by_hash_sql,
                (entity_id, content_hash),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch version by hash for {entity_id!r}"
            logger.warning(
                VERSION_FETCH_FAILED,
                table=self._table,
                entity_id=entity_id,
                content_hash=content_hash,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return self._deserialize_row(row)

    async def list_versions(
        self,
        entity_id: NotBlankStr,
        *,
        limit: int = _DEFAULT_LIST_LIMIT_50,
        offset: int = 0,
    ) -> tuple[VersionSnapshot[T], ...]:
        """List version snapshots ordered by version descending."""
        if limit < 0:
            msg = f"limit must be non-negative, got {limit}"
            raise ValueError(msg)
        if offset < 0:
            msg = f"offset must be non-negative, got {offset}"
            raise ValueError(msg)
        try:
            cursor = await self._db.execute(
                self._select_list_sql,
                (entity_id, limit, offset),
            )
            rows = list(await cursor.fetchall())
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to list versions for {entity_id!r}"
            logger.warning(
                VERSION_LIST_FAILED,
                table=self._table,
                entity_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            VERSION_LISTED,
            table=self._table,
            entity_id=entity_id,
            count=len(rows),
        )
        return tuple(self._deserialize_row(r) for r in rows)

    async def count_versions(self, entity_id: NotBlankStr) -> int:
        """Count version snapshots for an entity."""
        try:
            cursor = await self._db.execute(self._count_sql, (entity_id,))
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to count versions for {entity_id!r}"
            logger.warning(
                VERSION_COUNT_FAILED,
                table=self._table,
                entity_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(row[0]) if row else 0

    async def delete_versions_for_entity(self, entity_id: NotBlankStr) -> int:
        """Delete all version snapshots for an entity."""
        async with self._write_context():
            try:
                cursor = await self._db.execute(self._delete_sql, (entity_id,))
                await self._db.commit()
                count = cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to delete versions for {entity_id!r}"
                logger.warning(
                    VERSION_DELETE_FAILED,
                    table=self._table,
                    entity_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return count
