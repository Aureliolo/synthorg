"""Postgres append-only repository for the audit hash chain.

Mirrors the SQLite implementation; ``canonical_payload`` and
``signature`` are BYTEA columns (psycopg round-trips them as
``bytes`` / ``memoryview``).
"""

from datetime import datetime

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.audit_chain.chain import ChainEntry
from synthorg.observability.events.persistence.audit_chain_entry import (
    PERSISTENCE_AUDIT_CHAIN_ENTRY_APPEND_FAILED,
    PERSISTENCE_AUDIT_CHAIN_ENTRY_QUERIED,
    PERSISTENCE_AUDIT_CHAIN_ENTRY_QUERY_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc, validate_pagination_args
from synthorg.persistence.audit_chain_protocol import AuditChainFilterSpec

logger = get_logger(__name__)

_COLUMNS = (
    "chain_position, event_hash, previous_hash, canonical_payload, signature, timestamp"
)
_INSERT_SQL = """
INSERT INTO audit_chain_entries (
    chain_position, event_hash, previous_hash, canonical_payload, signature, timestamp
)
VALUES (%s, %s, %s, %s, %s, %s)
"""


def _to_bytes(value: object, *, column: str) -> bytes:
    """Coerce a BYTEA column value to ``bytes``.

    Returns:
        The column as ``bytes``.

    Raises:
        QueryError: If the value is not a bytes-like object.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    msg = f"audit_chain_entries.{column} is not bytes-like: {type(value).__name__}"
    raise QueryError(msg)


def _row_to_entry(row: DictRow) -> ChainEntry:
    """Deserialise an ``audit_chain_entries`` row into a ``ChainEntry``.

    Returns:
        The reconstructed ``ChainEntry``.

    Raises:
        QueryError: If a binary column is not bytes-like.
    """
    return ChainEntry(
        position=int(row["chain_position"]),
        event_hash=NotBlankStr(str(row["event_hash"])),
        previous_hash=NotBlankStr(str(row["previous_hash"])),
        canonical_payload=_to_bytes(
            row["canonical_payload"], column="canonical_payload"
        ),
        signature=_to_bytes(row["signature"], column="signature"),
        timestamp=normalize_utc(row["timestamp"]),
    )


class PostgresAuditChainRepository:
    """Postgres append-only audit-chain entry store.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool``.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, event: ChainEntry, /) -> None:
        """Append one chain entry at its ``position``.

        Raises:
            QueryError: If the write fails.
        """
        params: tuple[object, ...] = (
            event.position,
            str(event.event_hash),
            str(event.previous_hash),
            event.canonical_payload,
            event.signature,
            normalize_utc(event.timestamp),
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_INSERT_SQL, params)
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to append audit chain entry"
            logger.warning(
                PERSISTENCE_AUDIT_CHAIN_ENTRY_APPEND_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                position=event.position,
            )
            raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: AuditChainFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ChainEntry, ...]:
        """Return chain entries oldest-first by ``position``.

        Returns:
            Matching entries, ascending ``position``.

        Raises:
            QueryError: If the read fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_AUDIT_CHAIN_ENTRY_QUERY_FAILED
        )
        sql = f"SELECT {_COLUMNS} FROM audit_chain_entries"  # noqa: S608
        params: list[object] = []
        if filter_spec.min_position is not None:
            sql += " WHERE chain_position >= %s"
            params.append(filter_spec.min_position)
        sql += " ORDER BY chain_position ASC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, tuple(params))
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            raise self._read_error(exc) from exc
        try:
            entries = tuple(_row_to_entry(r) for r in rows)
        except QueryError:
            raise
        except Exception as exc:
            msg = "corrupt audit_chain_entries row(s)"
            logger.warning(
                PERSISTENCE_AUDIT_CHAIN_ENTRY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_AUDIT_CHAIN_ENTRY_QUERIED, count=len(entries))
        return entries

    async def get_tail(self) -> ChainEntry | None:
        """Return the highest-position entry, or ``None`` when empty.

        Returns:
            The newest entry, or ``None``.

        Raises:
            QueryError: If the read fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_COLUMNS} FROM audit_chain_entries "  # noqa: S608
                    "ORDER BY chain_position DESC LIMIT 1",
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            raise self._read_error(exc) from exc
        if row is None:
            return None
        return _row_to_entry(row)

    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete entries with ``timestamp < threshold``.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the delete fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM audit_chain_entries WHERE timestamp < %s",
                    (normalize_utc(threshold),),
                )
                removed = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            raise self._read_error(exc) from exc
        return removed

    def _read_error(self, exc: Exception) -> QueryError:
        msg = "Failed to read audit chain entries"
        logger.warning(
            PERSISTENCE_AUDIT_CHAIN_ENTRY_QUERY_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return QueryError(msg)
