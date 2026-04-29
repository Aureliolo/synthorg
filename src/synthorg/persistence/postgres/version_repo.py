"""Generic Postgres repository for versioned entity snapshots.

Parameterisable by entity type ``T`` via ``serialize_snapshot`` and
``deserialize_snapshot`` callables, so a single implementation covers
all versioned entities. Each entity type uses a dedicated table;
the table name is validated and injected at construction time.

For Postgres, the snapshot column is JSONB (not TEXT as in SQLite).
The callables convert between Pydantic models and dicts (not JSON
strings):
- serialize_snapshot: Callable[[T], dict]
- deserialize_snapshot: Callable[[dict], T]

Example::

    from synthorg.core.agent import AgentIdentity

    repo: PostgresVersionRepository[AgentIdentity] = PostgresVersionRepository(
        pool=pool,
        table_name="agent_identity_versions",
        serialize_snapshot=lambda m: m.model_dump(mode="json"),
        deserialize_snapshot=lambda d: AgentIdentity.model_validate(d),
    )
"""

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
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
from synthorg.versioning.models import VersionSnapshot

if TYPE_CHECKING:
    from collections.abc import Callable

    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

#: Allowed table name pattern -- lowercase letters, digits, underscores.
_TABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_SELECT_COLUMNS = "entity_id, version, content_hash, snapshot, saved_by, saved_at"


class PostgresVersionRepository[T: BaseModel]:
    """Postgres-backed generic version snapshot repository.

    All SQL queries are built at construction time using the validated
    ``table_name`` parameter. ``save_version`` uses ``INSERT ... ON
    CONFLICT DO NOTHING`` for idempotency.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
        table_name: Name of the SQL table storing snapshots for this
            entity type. Must match ``[a-z][a-z0-9_]*``.
        serialize_snapshot: Callable that converts a ``T`` instance to
            a dict (JSON-compatible) for persistence.
        deserialize_snapshot: Callable that converts a stored dict back
            to a ``T`` instance.
    """

    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        table_name: str,
        serialize_snapshot: Callable[[T], object],
        deserialize_snapshot: Callable[[object], T],
    ) -> None:
        if not _TABLE_NAME_RE.match(table_name):
            msg = f"Invalid table name: {table_name!r} (must match [a-z][a-z0-9_]*)"
            raise ValueError(msg)
        self._pool = pool
        self._table = table_name
        self._serialize = serialize_snapshot
        self._deserialize = deserialize_snapshot
        _t = self._table
        _c = _SELECT_COLUMNS
        self._insert_sql = (
            f"INSERT INTO {_t} "  # noqa: S608
            f"({_c}) VALUES (%s, %s, %s, %s, %s, %s) "
            f"ON CONFLICT (entity_id, version) DO NOTHING"
        )
        self._select_one_sql = (
            f"SELECT {_c} FROM {_t} WHERE entity_id = %s AND version = %s"  # noqa: S608
        )
        self._select_latest_sql = (
            f"SELECT {_c} FROM {_t} "  # noqa: S608
            f"WHERE entity_id = %s ORDER BY version DESC LIMIT 1"
        )
        self._select_by_hash_sql = (
            f"SELECT {_c} FROM {_t} "  # noqa: S608
            f"WHERE entity_id = %s AND content_hash = %s "
            f"ORDER BY version DESC LIMIT 1"
        )
        self._select_list_sql = (
            f"SELECT {_c} FROM {_t} "  # noqa: S608
            f"WHERE entity_id = %s ORDER BY version DESC LIMIT %s OFFSET %s"
        )
        self._count_sql = f"SELECT COUNT(*) FROM {_t} WHERE entity_id = %s"  # noqa: S608
        self._delete_sql = f"DELETE FROM {_t} WHERE entity_id = %s"  # noqa: S608

    def _deserialize_row(self, row: dict[str, object]) -> VersionSnapshot[T]:
        """Reconstruct a VersionSnapshot from a database row."""
        try:
            dt = row["saved_at"]
            if isinstance(dt, str):
                # Shouldn't happen with TIMESTAMPTZ, but be defensive
                dt_parsed = datetime.fromisoformat(dt)
                if dt_parsed.tzinfo is None:
                    dt_parsed = dt_parsed.replace(tzinfo=UTC)
            else:
                # TIMESTAMPTZ comes as datetime
                dt_parsed = dt  # type: ignore[assignment]
            entity_id_str: str = str(row["entity_id"])
            version_int: int = int(row["version"])  # type: ignore[call-overload]
            content_hash_str: str = str(row["content_hash"])
            saved_by_str: str = str(row["saved_by"])
            return VersionSnapshot(
                entity_id=entity_id_str,
                version=version_int,
                content_hash=content_hash_str,
                snapshot=self._deserialize(row["snapshot"]),
                saved_by=saved_by_str,
                saved_at=dt_parsed,
            )
        except ValidationError as exc:
            context = f"{row.get('entity_id', '?')}@v{row.get('version', '?')}"
            msg = f"Schema mismatch in version snapshot {context!r}: {exc}"
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
            context = f"{row.get('entity_id', '?')}@v{row.get('version', '?')}"
            msg = f"Failed to deserialize version snapshot {context!r}: {exc}"
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
            context = f"{row.get('entity_id', '?')}@v{row.get('version', '?')}"
            msg = f"Failed to deserialize version snapshot {context!r}: {exc}"
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
            was silently dropped by ``ON CONFLICT DO NOTHING``.

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
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    self._insert_sql,
                    (
                        version.entity_id,
                        version.version,
                        version.content_hash,
                        Jsonb(serialized),
                        version.saved_by,
                        version.saved_at,
                    ),
                )
                await conn.commit()
                inserted = cur.rowcount > 0
        except psycopg.Error as exc:
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
        return inserted

    async def get_version(
        self,
        entity_id: NotBlankStr,
        version: int,
    ) -> VersionSnapshot[T] | None:
        """Retrieve a specific version snapshot."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    self._select_one_sql,
                    (entity_id, version),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    self._select_latest_sql,
                    (entity_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    self._select_by_hash_sql,
                    (entity_id, content_hash),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        limit: int = 50,
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    self._select_list_sql,
                    (entity_id, limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(self._count_sql, (entity_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to count versions for {entity_id!r}"
            logger.warning(
                VERSION_COUNT_FAILED,
                table=self._table,
                entity_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(row["count"]) if row else 0

    async def delete_versions_for_entity(self, entity_id: NotBlankStr) -> int:
        """Delete all version snapshots for an entity."""
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(self._delete_sql, (entity_id,))
                await conn.commit()
                count = cur.rowcount
        except psycopg.Error as exc:
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
