"""Postgres-backed webhook receipt log repository.

Persists :class:`WebhookReceipt` rows in the ``webhook_receipts``
table for after-the-fact debugging and replay.  Reads are
newest-first and bounded by an explicit ``limit``.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import WebhookReceipt
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP,
    PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_FAILED,
    PERSISTENCE_WEBHOOK_RECEIPT_LIST_FAILED,
    PERSISTENCE_WEBHOOK_RECEIPT_LOG_FAILED,
)
from synthorg.persistence._shared import coerce_row_timestamp, normalize_utc
from synthorg.persistence.errors import QueryError

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


logger = get_logger(__name__)


_SELECT_COLS = (
    "id, connection_name, event_type, status, "
    "received_at, processed_at, payload_json, error"
)


def _row_to_receipt(row: dict[str, Any]) -> WebhookReceipt:
    """Deserialize a dict row into a :class:`WebhookReceipt`.

    The ``payload_json`` column is JSONB (a parsed value); the
    domain model expects a string, so re-serialize before
    constructing the model.
    """
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
        id=NotBlankStr(row["id"]),
        connection_name=NotBlankStr(row["connection_name"]),
        event_type=row.get("event_type") or "",
        status=row.get("status") or "received",
        received_at=coerce_row_timestamp(row["received_at"]),
        processed_at=(coerce_row_timestamp(processed_at) if processed_at else None),
        payload_json=payload_str,
        error=row.get("error"),
    )


class PostgresWebhookReceiptRepository:
    """Postgres implementation of :class:`WebhookReceiptRepository`."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        """Bind to the shared *pool*."""
        self._pool = pool

    async def log(self, receipt: WebhookReceipt) -> None:
        """Append a webhook receipt row (idempotent on receipt id)."""
        # ``payload_json`` is stored as JSONB; parse the model's
        # string representation at the boundary so reads can return
        # a structured value without a second parse.
        if receipt.payload_json:
            try:
                payload_obj: Any = json.loads(receipt.payload_json)
            except ValueError, TypeError:
                # Quarantine malformed JSON as a string under a stable key
                # so downstream readers still get a deserializable value.
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
    ) -> tuple[WebhookReceipt, ...]:
        """List receipts for *connection_name*, newest-first up to *limit*."""
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
                    "ORDER BY received_at DESC, id ASC LIMIT %s",
                    (str(connection_name), int(limit)),
                )
                rows = await cur.fetchall()
        except Exception as exc:
            msg = f"Failed to list webhook receipts for {connection_name!r}"
            logger.warning(
                PERSISTENCE_WEBHOOK_RECEIPT_LIST_FAILED,
                connection_name=str(connection_name),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(_row_to_receipt(row) for row in rows)

    async def cleanup_old(self, retention_days: int) -> int:
        """Delete receipts whose ``received_at`` is older than *retention_days*.

        ``retention_days <= 0`` is treated as a no-op so callers cannot
        accidentally truncate the entire log via misconfiguration.
        """
        if retention_days <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM webhook_receipts WHERE received_at < %s",
                    (cutoff,),
                )
                removed = cur.rowcount
        except Exception as exc:
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
