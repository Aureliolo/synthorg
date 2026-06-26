"""SQLite append-only repository for the audit hash chain.

Stores :class:`ChainEntry` rows keyed by their monotonic ``position``;
``canonical_payload`` and ``signature`` are raw bytes (BLOB). Reads are
oldest-first by ``position`` so the in-memory chain rebuilds in causal
order at startup.
"""

import sqlite3
from datetime import datetime

import aiosqlite

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
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence._shared.datetime_marshaller import (
    format_iso_utc,
    parse_iso_utc,
)
from synthorg.persistence.audit_chain_protocol import AuditChainFilterSpec
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_COLUMNS = (
    "chain_position, event_hash, previous_hash, canonical_payload, signature, timestamp"
)
_INSERT_SQL = """
INSERT INTO audit_chain_entries (
    chain_position, event_hash, previous_hash, canonical_payload, signature, timestamp
)
VALUES (?, ?, ?, ?, ?, ?)
"""


def _row_to_entry(row: dict[str, object]) -> ChainEntry:
    """Deserialise an ``audit_chain_entries`` row into a ``ChainEntry``.

    Returns:
        The reconstructed ``ChainEntry``.

    Raises:
        QueryError: If a binary column is not bytes.
    """
    payload = row["canonical_payload"]
    signature = row["signature"]
    if not isinstance(payload, (bytes, bytearray)) or not isinstance(
        signature, (bytes, bytearray)
    ):
        msg = "audit_chain_entries binary column is not bytes"
        raise QueryError(msg)
    return ChainEntry(
        position=int(row["chain_position"]),  # type: ignore[call-overload]
        event_hash=NotBlankStr(str(row["event_hash"])),
        previous_hash=NotBlankStr(str(row["previous_hash"])),
        canonical_payload=bytes(payload),
        signature=bytes(signature),
        timestamp=parse_iso_utc(str(row["timestamp"])),
    )


class SQLiteAuditChainRepository:
    """SQLite append-only audit-chain entry store.

    Args:
        db: An open ``aiosqlite.Connection``.
        write_context: Shared backend write context.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def append(self, event: ChainEntry, /) -> None:
        """Append one chain entry at its ``position``.

        Raises:
            QueryError: If the write fails.
        """
        params = (
            event.position,
            str(event.event_hash),
            str(event.previous_hash),
            event.canonical_payload,
            event.signature,
            format_iso_utc(event.timestamp),
        )
        async with self._write_context():
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                await self._db.execute(_INSERT_SQL, params)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
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
            sql += " WHERE chain_position >= ?"
            params.append(filter_spec.min_position)
        sql += " ORDER BY chain_position ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = list(await cursor.fetchall())
        except (sqlite3.Error, aiosqlite.Error) as exc:
            raise self._read_error(exc) from exc
        try:
            entries = tuple(_row_to_entry(dict(r)) for r in rows)
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
            async with self._db.execute(
                f"SELECT {_COLUMNS} FROM audit_chain_entries "  # noqa: S608
                "ORDER BY chain_position DESC LIMIT 1",
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            raise self._read_error(exc) from exc
        if row is None:
            return None
        return _row_to_entry(dict(row))

    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete entries with ``timestamp < threshold``.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the delete fails.
        """
        async with self._write_context():
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                async with self._db.execute(
                    "DELETE FROM audit_chain_entries WHERE timestamp < ?",
                    (format_iso_utc(threshold),),
                ) as cursor:
                    removed = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
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

    async def _safe_rollback(self) -> None:
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            logger.warning(
                PERSISTENCE_AUDIT_CHAIN_ENTRY_APPEND_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                rollback_failed=True,
            )
