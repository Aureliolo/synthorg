"""SQLite append-only repository for promotion/demotion history.

The nested ``PromotionRecord`` round-trips through a single JSON
``payload`` column; ``agent_id`` / ``direction`` / ``effective_at`` are
promoted to columns for filtering and recency ordering.
"""

import json
import sqlite3
from datetime import datetime

import aiosqlite

from synthorg.core.persistence_errors import QueryError
from synthorg.hr.promotion.models import PromotionRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.promotion_history import (
    PERSISTENCE_PROMOTION_HISTORY_APPEND_FAILED,
    PERSISTENCE_PROMOTION_HISTORY_DESERIALIZE_FAILED,
    PERSISTENCE_PROMOTION_HISTORY_QUERIED,
    PERSISTENCE_PROMOTION_HISTORY_QUERY_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence._shared.datetime_marshaller import (
    format_iso_utc,
)
from synthorg.persistence.promotion_history_protocol import (
    PromotionHistoryFilterSpec,
)
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_INSERT_SQL = """
INSERT INTO promotion_history (id, agent_id, direction, effective_at, payload)
VALUES (?, ?, ?, ?, ?)
"""
_SELECT_SQL = "SELECT payload FROM promotion_history"


def _record_to_params(record: PromotionRecord) -> tuple[object, ...]:
    """Marshal a ``PromotionRecord`` into positional INSERT params.

    Returns:
        Positional params: ``(id, agent_id, direction, effective_at, payload)``.
    """
    return (
        str(record.id),
        str(record.agent_id),
        record.direction.value,
        format_iso_utc(record.effective_at),
        json.dumps(record.model_dump(mode="json"), sort_keys=True),
    )


def _row_to_record(payload: object) -> PromotionRecord:
    """Deserialise a JSON ``payload`` column into a ``PromotionRecord``.

    Returns:
        The reconstructed ``PromotionRecord``.

    Raises:
        QueryError: If the payload is not a JSON object.
    """
    data = json.loads(str(payload)) if payload else {}
    if not isinstance(data, dict):
        msg = f"promotion_history.payload is not a JSON object: {data!r}"
        raise QueryError(msg)
    return PromotionRecord.model_validate(data)


class SQLitePromotionHistoryRepository:
    """SQLite append-only promotion/demotion history store.

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

    async def append(self, event: PromotionRecord, /) -> None:
        """Append one immutable promotion record.

        Raises:
            QueryError: If the write fails.
        """
        async with self._write_context():
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                await self._db.execute(_INSERT_SQL, _record_to_params(event))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = "Failed to append promotion record"
                logger.warning(
                    PERSISTENCE_PROMOTION_HISTORY_APPEND_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    agent_id=str(event.agent_id),
                )
                raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: PromotionHistoryFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[PromotionRecord, ...]:
        """Return promotion records matching the filter, newest-first.

        Returns:
            Matching records, newest-first by ``effective_at``.

        Raises:
            QueryError: If the read fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_PROMOTION_HISTORY_QUERY_FAILED
        )
        sql = _SELECT_SQL
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(str(filter_spec.agent_id))
        if filter_spec.direction is not None:
            clauses.append("direction = ?")
            params.append(filter_spec.direction.value)
        if filter_spec.since is not None:
            clauses.append("effective_at >= ?")
            params.append(format_iso_utc(filter_spec.since))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY effective_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = list(await cursor.fetchall())
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query promotion history"
            logger.warning(
                PERSISTENCE_PROMOTION_HISTORY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            records = tuple(_row_to_record(dict(r)["payload"]) for r in rows)
        except Exception as exc:
            msg = "corrupt promotion_history row(s)"
            logger.warning(
                PERSISTENCE_PROMOTION_HISTORY_DESERIALIZE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_PROMOTION_HISTORY_QUERIED,
            count=len(records),
        )
        return records

    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete records with ``effective_at < threshold``.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If *threshold* is naive or the delete fails.
        """
        if threshold.tzinfo is None:
            msg = "promotion history purge threshold must be timezone-aware"
            raise QueryError(msg)
        async with self._write_context():
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                async with self._db.execute(
                    "DELETE FROM promotion_history WHERE effective_at < ?",
                    (format_iso_utc(threshold),),
                ) as cursor:
                    removed = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = "Failed to purge promotion history"
                logger.warning(
                    PERSISTENCE_PROMOTION_HISTORY_QUERY_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return removed

    async def _safe_rollback(self) -> None:
        """Best-effort rollback on the shared connection."""
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            logger.warning(
                PERSISTENCE_PROMOTION_HISTORY_APPEND_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                rollback_failed=True,
            )
