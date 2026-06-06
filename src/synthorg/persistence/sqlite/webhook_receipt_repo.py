"""SQLite-backed webhook receipt log repository.

Persists :class:`WebhookReceipt` rows in the ``webhook_receipts``
table for after-the-fact debugging and replay.  Reads are
newest-first and bounded by an explicit ``limit``.
"""

import contextlib
import sqlite3
from datetime import UTC, datetime, timedelta

import aiosqlite

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import WebhookReceipt
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.webhook_receipt import (
    PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP,
    PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_FAILED,
    PERSISTENCE_WEBHOOK_RECEIPT_DELETE_FAILED,
    PERSISTENCE_WEBHOOK_RECEIPT_LIST_FAILED,
    PERSISTENCE_WEBHOOK_RECEIPT_LOG_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)


_SELECT_COLS = (
    "id, connection_name, event_type, status, "
    "received_at, processed_at, payload_json, error"
)


def _row_to_receipt(row: aiosqlite.Row) -> WebhookReceipt:
    """Deserialize a row tuple into a :class:`WebhookReceipt`.

    Returns:
        Result of type ``WebhookReceipt``.
    """
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
        write_context: WriteContext,
    ) -> None:
        """Bind to *db* and serialize writes via *write_context*."""
        self._db = db
        self._write_context = write_context

    async def save(self, entity: WebhookReceipt) -> None:
        """Persist a webhook receipt (idempotent on receipt id).

        Raises:
            QueryError: If the database query fails.
        """
        receipt = entity
        async with self._write_context():
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

    async def get(self, receipt_id: NotBlankStr) -> WebhookReceipt | None:
        """Fetch a single receipt by ID, or ``None`` when absent.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._db.execute(
                f"SELECT {_SELECT_COLS} FROM webhook_receipts "  # noqa: S608
                "WHERE id = ?",
                (str(receipt_id),),
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch webhook receipt {receipt_id!r}"
            logger.warning(
                PERSISTENCE_WEBHOOK_RECEIPT_LIST_FAILED,
                receipt_id=str(receipt_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        try:
            return _row_to_receipt(row)
        except (ValueError, TypeError, KeyError) as exc:
            msg = f"Failed to deserialize webhook receipt {receipt_id!r}"
            logger.warning(
                PERSISTENCE_WEBHOOK_RECEIPT_LIST_FAILED,
                receipt_id=str(receipt_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def update_status(
        self,
        receipt_id: NotBlankStr,
        *,
        status: str,
        processed_at: datetime | None,
        error: str | None,
    ) -> bool:
        """Update the status / processed_at / error of an existing receipt.

        Returns ``True`` when the row existed and was updated, ``False``
        when no row matched the ID. Callers can use the boolean to
        distinguish "not found" from a successful no-op.

        Returns:
            True when the operation succeeded, False otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "UPDATE webhook_receipts SET "
                    "status = ?, processed_at = ?, error = ? "
                    "WHERE id = ?",
                    (
                        status,
                        format_iso_utc(processed_at) if processed_at else None,
                        error,
                        str(receipt_id),
                    ),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to update webhook receipt {receipt_id!r}"
                logger.warning(
                    PERSISTENCE_WEBHOOK_RECEIPT_LOG_FAILED,
                    receipt_id=str(receipt_id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            else:
                return cursor.rowcount > 0

    async def update_status_if_current(
        self,
        receipt_id: NotBlankStr,
        *,
        expected_status: str,
        status: str,
        processed_at: datetime | None,
        error: str | None,
    ) -> bool:
        """Compare-and-set ``status`` only when the current row matches.

        Adds a ``status = ?`` clause to the WHERE so two concurrent
        retries can never both succeed -- one wins, one returns
        ``False`` (rowcount 0) and the caller handles the lost-race
        path. SQLite's per-write_context serialization gives the
        ordering guarantee; the WHERE clause supplies the predicate.

        Returns:
            True when the operation succeeded, False otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "UPDATE webhook_receipts SET "
                    "status = ?, processed_at = ?, error = ? "
                    "WHERE id = ? AND status = ?",
                    (
                        status,
                        format_iso_utc(processed_at) if processed_at else None,
                        error,
                        str(receipt_id),
                        expected_status,
                    ),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to CAS webhook receipt {receipt_id!r}"
                logger.warning(
                    PERSISTENCE_WEBHOOK_RECEIPT_LOG_FAILED,
                    receipt_id=str(receipt_id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            else:
                return cursor.rowcount > 0

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[WebhookReceipt, ...]:
        """List all webhook receipts with pagination.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_WEBHOOK_RECEIPT_LIST_FAILED
        )
        sql = (
            "SELECT "
            "    id, connection_name, event_type, status, "
            "    received_at, processed_at, payload_json, error "
            "FROM webhook_receipts ORDER BY received_at DESC, id DESC"
        )
        sql += " LIMIT ? OFFSET ?"
        params: tuple[object, ...] = (limit, offset)
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list webhook receipts"
            logger.warning(
                PERSISTENCE_WEBHOOK_RECEIPT_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            return tuple(_row_to_receipt(row) for row in rows)
        except (ValueError, TypeError, KeyError) as exc:
            msg = "Failed to deserialize webhook receipt rows"
            logger.warning(
                PERSISTENCE_WEBHOOK_RECEIPT_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a webhook receipt by ID.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM webhook_receipts WHERE id = ?",
                    (str(entity_id),),
                )
                deleted = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to delete webhook receipt {entity_id!r}"
                logger.warning(
                    PERSISTENCE_WEBHOOK_RECEIPT_DELETE_FAILED,
                    receipt_id=str(entity_id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return deleted

    async def get_by_connection(
        self,
        connection_name: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[WebhookReceipt, ...]:
        """List receipts for *connection_name*, newest-first up to *limit*.

        Returns:
            Tuple of matching rows; empty when no rows match.

        Raises:
            QueryError: If the database query fails.
        """
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

    async def cleanup_old_for_connection(
        self,
        connection_name: NotBlankStr,
        retention_days: int,
    ) -> int:
        """Delete receipts for *connection_name* older than *retention_days*.

        ``retention_days <= 0`` is treated as a no-op so callers cannot
        accidentally truncate the log via misconfiguration.

        Note: holds ``self._write_context()`` for the duration of the DELETE
        + COMMIT.  On a large ``webhook_receipts`` table this can
        briefly block other writers (the daily sweep is serialised
        against the rest of the SQLite write traffic by design).
        Batching the delete is left as a future optimisation; current
        deployment scale (handful of connections, days-scale retention)
        keeps each per-connection sweep small.

        Returns:
            Numeric result of the operation.

        Raises:
            QueryError: If the database query fails.
        """
        if retention_days <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        cutoff_iso = format_iso_utc(cutoff)
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM webhook_receipts "
                    "WHERE connection_name = ? AND received_at < ?",
                    (str(connection_name), cutoff_iso),
                )
                removed = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to cleanup old webhook receipts for {connection_name!r}"
                logger.warning(
                    PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_FAILED,
                    connection_name=str(connection_name),
                    retention_days=retention_days,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        if removed:
            logger.info(
                PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP,
                connection_name=str(connection_name),
                retention_days=retention_days,
                removed=removed,
            )
        return removed
