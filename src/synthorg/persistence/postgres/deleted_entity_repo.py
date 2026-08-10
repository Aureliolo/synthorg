# module-kind: repository
"""Postgres implementation of the ``DeletedEntityRepository`` protocol.

Postgres sibling of ``persistence/sqlite/deleted_entity_repo.py``.
``deleted_at`` is stored as TIMESTAMPTZ.
"""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

from datetime import datetime
from typing import Final, LiteralString

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from synthorg.core.deleted_entity import DeletedEntity
from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.deleted_entity import (
    PERSISTENCE_DELETED_ENTITY_APPEND_FAILED,
    PERSISTENCE_DELETED_ENTITY_DESERIALIZE_FAILED,
    PERSISTENCE_DELETED_ENTITY_PURGE_FAILED,
    PERSISTENCE_DELETED_ENTITY_QUERIED,
    PERSISTENCE_DELETED_ENTITY_QUERY_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.deleted_entity_protocol import DeletedEntityFilterSpec

logger = get_logger(__name__)

_COLUMNS: Final[LiteralString] = (
    "id, entity_kind, entity_id, display_name, deleted_by, deleted_at"
)

#: Idempotent on the row's own id, which the caller mints per tombstone. A
#: teardown re-issued after a lost response writes the same object, and a
#: plain INSERT would answer that retry with a duplicate-key error on the one
#: table whose whole job is to still be there afterwards.
_INSERT_SQL: Final[LiteralString] = f"""\
INSERT INTO deleted_entities ({_COLUMNS}) VALUES (
    %(id)s, %(entity_kind)s, %(entity_id)s, %(display_name)s, %(deleted_by)s,
    %(deleted_at)s
) ON CONFLICT (id) DO NOTHING"""


class PostgresDeletedEntityRepository:
    """Postgres implementation of ``DeletedEntityRepository``.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, event: DeletedEntity) -> None:
        """Persist one tombstone (append-only).

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_INSERT_SQL, _to_row(event))
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to record deletion of {event.entity_id!r}"
            logger.warning(
                PERSISTENCE_DELETED_ENTITY_APPEND_FAILED,
                entity_kind=event.entity_kind.value,
                entity_id=event.entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: DeletedEntityFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DeletedEntity, ...]:
        """Return tombstones matching the filter, newest-first.

        Returns:
            The matching tombstones.

        Raises:
            QueryError: If the database query fails or a row is malformed.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_DELETED_ENTITY_QUERY_FAILED
        )
        clauses: list[LiteralString] = []
        params: list[object] = []
        if filter_spec.entity_kind is not None:
            clauses.append("entity_kind = %s")
            params.append(filter_spec.entity_kind.value)
        if filter_spec.entity_id is not None:
            clauses.append("entity_id = %s")
            params.append(filter_spec.entity_id)
        where: LiteralString = " AND ".join(clauses) if clauses else "TRUE"
        sql: LiteralString = (
            f"SELECT {_COLUMNS} FROM deleted_entities WHERE {where} "
            "ORDER BY deleted_at DESC, id DESC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, [*params, limit, offset])
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query deleted entities"
            logger.warning(
                PERSISTENCE_DELETED_ENTITY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        tombstones = tuple(_row_to_model(r) for r in rows)
        logger.debug(PERSISTENCE_DELETED_ENTITY_QUERIED, count=len(tombstones))
        return tombstones

    async def purge_before(self, threshold: datetime) -> int:
        """Delete tombstones with ``deleted_at < threshold``.

        Args:
            threshold: Timezone-aware UTC timestamp. A naive datetime is
                rejected to prevent silent local-time misinterpretation
                deleting the wrong retention window.

        Returns:
            Number of rows deleted.

        Raises:
            QueryError: If *threshold* is naive or the query fails.
        """
        if threshold.tzinfo is None:
            msg = "threshold must be timezone-aware; a naive datetime is rejected"
            raise QueryError(msg)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM deleted_entities WHERE deleted_at < %s",
                    (normalize_utc(threshold),),
                )
                count = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge deleted entities by threshold"
            logger.warning(
                PERSISTENCE_DELETED_ENTITY_PURGE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return count


def _to_row(event: DeletedEntity) -> dict[str, object]:
    """Flatten a tombstone into a row dict.

    Returns:
        The row as bound parameters.
    """
    data = event.model_dump(mode="json")
    data["deleted_at"] = normalize_utc(event.deleted_at)
    return data


def _row_to_model(row: DictRow) -> DeletedEntity:
    """Convert a database row to a ``DeletedEntity``.

    Returns:
        The deserialized tombstone.

    Raises:
        QueryError: If the row cannot be deserialized.
    """
    try:
        data = dict(row)
        # psycopg returns TIMESTAMPTZ in the session timezone, not necessarily
        # UTC; normalise on read so the model carries a UTC instant.
        data["deleted_at"] = normalize_utc(data["deleted_at"])
        return DeletedEntity.model_validate(data)
    except ValidationError as exc:
        msg = f"Failed to deserialize tombstone {row.get('id')!r}"
        logger.warning(
            PERSISTENCE_DELETED_ENTITY_DESERIALIZE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc
