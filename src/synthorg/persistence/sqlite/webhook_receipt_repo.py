"""SQLite-backed webhook receipt log repository.

Persists :class:`WebhookReceipt` rows in the ``webhook_receipts``
table for after-the-fact debugging and replay.  Reads are
newest-first and bounded by an explicit ``limit``.
"""

import asyncio
import contextlib
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import WebhookReceipt
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP,
    PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_FAILED,
    PERSISTENCE_WEBHOOK_RECEIPT_LIST_FAILED,
    PERSISTENCE_WEBHOOK_RECEIPT_LOG_FAILED,
)
from synthorg.persistence._shared import coerce_row_timestamp, format_iso_utc

logger = get_logger(__name__)


_SELECT_COLS = (
    "id, connection_name, event_type, status, "
    "received_at, processed_at, payload_json, error"
)


def _row_to_receipt(row: aiosqlite.Row | tuple[Any, ...]) -> WebhookReceipt:
    """Deserialize a row tuple into a :class:`WebhookReceipt`."""
    (
        receipt_id,
        connection_name,
        event_type,
        status,
        received_at,
        processed_at,
        payload_json,
        error,
    ) = row
    return WebhookReceipt(
        id=NotBlankStr(receipt_id),
        connection_name=NotBlankStr(connection_name),
        event_type=event_type or "",
        status=status or "received",
        received_at=coerce_row_timestamp(received_at),
        processed_at=(coerce_row_timestamp(processed_at) if processed_at else None),
        payload_json=payload_json or "",
        error=error,
    )


class SQLiteWebhookReceiptRepository:
    """SQLite implementation of :class:`WebhookReceiptRepository`."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        """Bind to *db* and serialize writes via *write_lock*."""
        self._db = db
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

    async def log(self, receipt: WebhookReceipt) -> None:
        """Append a webhook receipt row (idempotent on receipt id)."""
        async with self._write_lock:
            try:
                await self._db.execute(
                    """
                    INSERT INTO webhook_receipts (
                        id, connection_name, event_type, status,
                        received_at, processed_at, payload_json, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        connection_name = excluded.connection_name,
                        event_type = excluded.event_type,
                        status = excluded.status,
                        received_at = excluded.received_at,
                        processed_at = excluded.processed_at,
                        payload_json = excluded.payload_json,
                        error = excluded.error
                    """,
                    (
                        str(receipt.id),
                        str(receipt.connection_name),
                        receipt.event_type,
                        receipt.status,
                        format_iso_utc(receipt.received_at),
                        (
                            format_iso_utc(receipt.processed_at)
                            if receipt.processed_at
                            else None
                        ),
                        receipt.payload_json,
                        receipt.error,
                    ),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to log webhook receipt {receipt.id!r}"
                logger.warning(
                    PERSISTENCE_WEBHOOK_RECEIPT_LOG_FAILED,
                    receipt_id=str(receipt.id),
                    connection_name=str(receipt.connection_name),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get_by_connection(
        self,
        connection_name: NotBlankStr,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[WebhookReceipt, ...]:
        """List receipts for *connection_name*, newest-first up to *limit*."""
        if limit <= 0:
            return ()
        try:
            async with self._db.execute(
                f"SELECT {_SELECT_COLS} FROM webhook_receipts "  # noqa: S608
                "WHERE connection_name = ? "
                "ORDER BY received_at DESC, id ASC LIMIT ? OFFSET ?",
                (str(connection_name), int(limit), max(0, int(offset))),
            ) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to list webhook receipts for {connection_name!r}"
            logger.warning(
                PERSISTENCE_WEBHOOK_RECEIPT_LIST_FAILED,
                connection_name=str(connection_name),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            return tuple(_row_to_receipt(row) for row in rows)
        except (ValueError, TypeError, KeyError) as exc:
            msg = f"Failed to deserialize webhook receipts for {connection_name!r}"
            logger.warning(
                PERSISTENCE_WEBHOOK_RECEIPT_LIST_FAILED,
                connection_name=str(connection_name),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def cleanup_old(self, retention_days: int) -> int:
        """Delete receipts whose ``received_at`` is older than *retention_days*.

        ``retention_days <= 0`` is treated as a no-op so callers cannot
        accidentally truncate the entire log via misconfiguration.
        """
        if retention_days <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        cutoff_iso = format_iso_utc(cutoff)
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "DELETE FROM webhook_receipts WHERE received_at < ?",
                    (cutoff_iso,),
                )
                removed = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = "Failed to cleanup old webhook receipts"
                logger.warning(
                    PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_FAILED,
                    retention_days=retention_days,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        if removed:
            logger.info(
                PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP,
                retention_days=retention_days,
                removed=removed,
            )
        return removed
