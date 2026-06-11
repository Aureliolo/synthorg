"""Postgres-backed webhook receipt log repository.

Persists :class:`WebhookReceipt` rows in the ``webhook_receipts``
table for after-the-fact debugging and replay.  Reads are
newest-first and bounded by an explicit ``limit``.
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import MalformedRowError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import WebhookReceipt
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.webhook_receipt import (
    PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP,
    PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_FAILED,
    PERSISTENCE_WEBHOOK_RECEIPT_DELETE_FAILED,
    PERSISTENCE_WEBHOOK_RECEIPT_DESERIALIZE_FAILED,
    PERSISTENCE_WEBHOOK_RECEIPT_LIST_FAILED,
    PERSISTENCE_WEBHOOK_RECEIPT_LOG_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    normalize_utc,
    validate_pagination_args,
)

logger = get_logger(__name__)


_SELECT_COLS = (
    "id, connection_name, event_type, status, "
    "received_at, processed_at, payload_json, error"
)


def _row_to_receipt(row: DictRow) -> WebhookReceipt:
    """Deserialize a dict row into a :class:`WebhookReceipt`.

    The ``payload_json`` column is JSONB (a parsed value); the
    domain model expects a string, so re-serialize before
    constructing the model.

    Returns:
        Result of type ``WebhookReceipt``.

    Raises:
        MalformedRowError: If the row cannot be parsed (e.g. a stored
            ``id`` that is not a valid UUID, or a missing column). The
            failure is deterministic, so it is non-retryable.
    """
    try:
        raw_payload = row.get("payload_json")
        if raw_payload is None:
            payload_str = ""
        elif isinstance(raw_payload, str):
            payload_str = raw_payload
        else:
            # Use compact separators so a round-trip through JSONB produces a
            # string that compares equal byte-for-byte with the caller's
            # compact input (the model contract is "string of JSON", not a
            # canonical pretty-print).
            payload_str = json.dumps(raw_payload, separators=(",", ":"))
        processed_at = row.get("processed_at")
        return WebhookReceipt(
            id=UUID(str(row["id"])),
            connection_name=NotBlankStr(row["connection_name"]),
            event_type=row.get("event_type") or "",
            status=row.get("status") or "received",
            received_at=coerce_row_timestamp(row["received_at"]),
            processed_at=(coerce_row_timestamp(processed_at) if processed_at else None),
            payload_json=payload_str,
            error=row.get("error"),
        )
    except (ValueError, TypeError, KeyError) as exc:
        row_id = row.get("id", "<missing>")
        msg = f"Failed to deserialize webhook receipt {row_id!r}"
        logger.warning(
            PERSISTENCE_WEBHOOK_RECEIPT_DESERIALIZE_FAILED,
            receipt_id=str(row_id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise MalformedRowError(msg) from exc


class PostgresWebhookReceiptRepository:
    """Postgres implementation of :class:`WebhookReceiptRepository`."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        """Bind to the shared *pool*."""
        self._pool = pool

    async def save(self, entity: WebhookReceipt) -> None:
        """Persist a webhook receipt (idempotent on receipt id).

        Raises:
            QueryError: If the database query fails.
        """
        receipt = entity
        # ``payload_json`` is stored as JSONB; parse the model's
        # string representation at the boundary so reads can return
        # a structured value without a second parse.
        if receipt.payload_json:
            try:
                payload_obj: object = json.loads(receipt.payload_json)
            except (ValueError, TypeError) as exc:
                # Quarantine malformed JSON as a string under a stable key so
                # downstream readers still get a deserializable value.  Log
                # at WARNING so operators see a signal that an upstream
                # webhook source is emitting non-JSON payloads; without this
                # the corruption is invisible.
                logger.warning(
                    PERSISTENCE_WEBHOOK_RECEIPT_LOG_FAILED,
                    receipt_id=str(receipt.id),
                    connection_name=str(receipt.connection_name),
                    reason="payload_json_malformed_quarantined",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                payload_obj = {"raw": receipt.payload_json}
        else:
            payload_obj = {}
        params = (
            str(receipt.id),
            str(receipt.connection_name),
            receipt.event_type,
            receipt.status,
            normalize_utc(receipt.received_at),
            (normalize_utc(receipt.processed_at) if receipt.processed_at else None),
            Jsonb(payload_obj),
            receipt.error,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO webhook_receipts (
                        id, connection_name, event_type, status,
                        received_at, processed_at, payload_json, error
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        connection_name = EXCLUDED.connection_name,
                        event_type = EXCLUDED.event_type,
                        status = EXCLUDED.status,
                        received_at = EXCLUDED.received_at,
                        processed_at = EXCLUDED.processed_at,
                        payload_json = EXCLUDED.payload_json,
                        error = EXCLUDED.error
                    """,
                    params,
                )
        except Exception as exc:
            reraise_critical(exc)
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
            MalformedRowError: If the stored row cannot be deserialized.
            QueryError: If the database query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLS} FROM webhook_receipts "  # noqa: S608
                    "WHERE id = %s",
                    (str(receipt_id),),
                )
                row = await cur.fetchone()
        except Exception as exc:
            reraise_critical(exc)
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
        return _row_to_receipt(row)

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
        when no row matched the ID.

        Returns:
            True when the operation succeeded, False otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "UPDATE webhook_receipts SET "
                    "status = %s, processed_at = %s, error = %s "
                    "WHERE id = %s",
                    (
                        status,
                        normalize_utc(processed_at) if processed_at else None,
                        error,
                        str(receipt_id),
                    ),
                )
                return cur.rowcount > 0
        except Exception as exc:
            reraise_critical(exc)
            msg = f"Failed to update webhook receipt {receipt_id!r}"
            logger.warning(
                PERSISTENCE_WEBHOOK_RECEIPT_LOG_FAILED,
                receipt_id=str(receipt_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

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

        Adds ``AND status = %s`` to the WHERE so two concurrent retries
        cannot both flip the same row from ``received`` to ``retrying``
        and re-publish the captured payload twice. The UPDATE runs in
        its own transaction so the row-level lock pgsql acquires for
        the matching row serialises the racing callers; the loser sees
        ``rowcount == 0`` and the controller raises ``NotFoundError``
        instead of re-publishing.

        Returns:
            True when the operation succeeded, False otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "UPDATE webhook_receipts SET "
                    "status = %s, processed_at = %s, error = %s "
                    "WHERE id = %s AND status = %s",
                    (
                        status,
                        normalize_utc(processed_at) if processed_at else None,
                        error,
                        str(receipt_id),
                        expected_status,
                    ),
                )
                return cur.rowcount > 0
        except Exception as exc:
            reraise_critical(exc)
            msg = f"Failed to CAS webhook receipt {receipt_id!r}"
            logger.warning(
                PERSISTENCE_WEBHOOK_RECEIPT_LOG_FAILED,
                receipt_id=str(receipt_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

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
            MalformedRowError: If any stored row cannot be deserialized.
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_WEBHOOK_RECEIPT_LIST_FAILED
        )
        sql = (
            "SELECT id, connection_name, event_type, status, "
            "       received_at, processed_at, payload_json, error "
            "FROM webhook_receipts ORDER BY received_at DESC, id DESC "
            "LIMIT %s OFFSET %s"
        )
        params: tuple[object, ...] = (limit, offset)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except Exception as exc:
            reraise_critical(exc)
            msg = "Failed to list webhook receipts"
            logger.warning(
                PERSISTENCE_WEBHOOK_RECEIPT_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(_row_to_receipt(row) for row in rows)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a webhook receipt by ID.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM webhook_receipts WHERE id = %s",
                    (str(entity_id),),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except Exception as exc:
            reraise_critical(exc)
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
            MalformedRowError: If any stored row cannot be deserialized.
            QueryError: If the database query fails.
        """
        if limit <= 0:
            return ()
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLS} FROM webhook_receipts "  # noqa: S608
                    "WHERE connection_name = %s "
                    "ORDER BY received_at DESC, id ASC LIMIT %s OFFSET %s",
                    (str(connection_name), int(limit), max(0, int(offset))),
                )
                rows = await cur.fetchall()
        except Exception as exc:
            reraise_critical(exc)
            msg = f"Failed to list webhook receipts for {connection_name!r}"
            logger.warning(
                PERSISTENCE_WEBHOOK_RECEIPT_LIST_FAILED,
                connection_name=str(connection_name),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(_row_to_receipt(row) for row in rows)

    async def cleanup_old_for_connection(
        self,
        connection_name: NotBlankStr,
        retention_days: int,
    ) -> int:
        """Delete receipts for *connection_name* older than *retention_days*.

        ``retention_days <= 0`` is treated as a no-op so callers cannot
        accidentally truncate the log via misconfiguration.

        Returns:
            Numeric result of the operation.

        Raises:
            QueryError: If the database query fails.
        """
        if retention_days <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM webhook_receipts "
                    "WHERE connection_name = %s AND received_at < %s",
                    (str(connection_name), normalize_utc(cutoff)),
                )
                removed = cur.rowcount
        except Exception as exc:
            reraise_critical(exc)
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
