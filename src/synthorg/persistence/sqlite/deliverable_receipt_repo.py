"""SQLite repository implementation for deliverable receipts."""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

import contextlib
import sqlite3

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.deliverable_receipts.models import DeliverableReceipt
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.deliverable_receipts import (
    PERSISTENCE_RECEIPT_DELETE_FAILED,
    PERSISTENCE_RECEIPT_DESERIALIZE_FAILED,
    PERSISTENCE_RECEIPT_QUERY_FAILED,
    PERSISTENCE_RECEIPT_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared.datetime_marshaller import format_iso_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.deliverable_receipt_protocol import (
    DeliverableReceiptFilterSpec,
)
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_COLUMNS = (
    "receipt_id, task_id, project_id, execution_id, deliverable_doc_slug, "
    "issued_at, total_cost, currency, payload_json"
)

_UPSERT_SQL = f"""\
INSERT INTO deliverable_receipt ({_COLUMNS}) VALUES (
    :receipt_id, :task_id, :project_id, :execution_id, :deliverable_doc_slug,
    :issued_at, :total_cost, :currency, :payload_json
)
ON CONFLICT(task_id) DO UPDATE SET
    receipt_id = excluded.receipt_id,
    project_id = excluded.project_id,
    execution_id = excluded.execution_id,
    deliverable_doc_slug = excluded.deliverable_doc_slug,
    issued_at = excluded.issued_at,
    total_cost = excluded.total_cost,
    currency = excluded.currency,
    payload_json = excluded.payload_json
"""


class SQLiteDeliverableReceiptRepository:
    """SQLite implementation of ``DeliverableReceiptRepository``.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager that serializes writes on
            the shared connection.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def save(self, entity: DeliverableReceipt) -> None:
        """Persist a receipt via upsert keyed on ``task_id``.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(_UPSERT_SQL, self._to_row(entity))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to save deliverable receipt {entity.receipt_id!r}"
                logger.warning(
                    PERSISTENCE_RECEIPT_SAVE_FAILED,
                    receipt_id=entity.receipt_id,
                    task_id=entity.task_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> DeliverableReceipt | None:
        """Retrieve a receipt by ``receipt_id``.

        Returns:
            The receipt, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            cursor = await self._db.execute(
                "SELECT payload_json FROM deliverable_receipt WHERE receipt_id = ?",
                (entity_id,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to load deliverable receipt {entity_id!r}"
            logger.warning(
                PERSISTENCE_RECEIPT_QUERY_FAILED,
                receipt_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return self._row_to_model(dict(row))

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a receipt by ``receipt_id``.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM deliverable_receipt WHERE receipt_id = ?",
                    (entity_id,),
                )
                deleted = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to delete deliverable receipt {entity_id!r}"
                logger.warning(
                    PERSISTENCE_RECEIPT_DELETE_FAILED,
                    receipt_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return deleted

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DeliverableReceipt, ...]:
        """List receipts across projects, most-recent first.

        Returns:
            Receipts ordered by descending ``issued_at``.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_RECEIPT_QUERY_FAILED
        )
        try:
            cursor = await self._db.execute(
                "SELECT payload_json FROM deliverable_receipt "
                "ORDER BY issued_at DESC, receipt_id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list deliverable receipts"
            logger.warning(
                PERSISTENCE_RECEIPT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_model(dict(r)) for r in rows)

    async def query(
        self,
        filter_spec: DeliverableReceiptFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DeliverableReceipt, ...]:
        """Return receipts matching the filter, most-recent first.

        Returns:
            Matching receipts ordered by descending ``issued_at``.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_RECEIPT_QUERY_FAILED
        )
        where, params = self._build_where(filter_spec)
        sql = (
            f"SELECT payload_json FROM deliverable_receipt WHERE {where} "
            "ORDER BY issued_at DESC, receipt_id DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query deliverable receipts"
            logger.warning(
                PERSISTENCE_RECEIPT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_model(dict(r)) for r in rows)

    async def count(self, filter_spec: DeliverableReceiptFilterSpec) -> int:
        """Count receipts matching the filter spec.

        Returns:
            Number of matching receipts.

        Raises:
            QueryError: If the database query fails.
        """
        where, params = self._build_where(filter_spec)
        try:
            cursor = await self._db.execute(
                f"SELECT COUNT(*) AS n FROM deliverable_receipt WHERE {where}",
                params,
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to count deliverable receipts"
            logger.warning(
                PERSISTENCE_RECEIPT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(dict(row)["n"]) if row is not None else 0

    def _build_where(
        self, filter_spec: DeliverableReceiptFilterSpec
    ) -> tuple[str, list[object]]:
        """Build the WHERE clause + positional params for ``filter_spec``.

        Returns:
            ``(where_clause, params)`` without the leading ``WHERE``.
        """
        conditions: list[str] = ["project_id = ?"]
        params: list[object] = [filter_spec.project_id]
        if filter_spec.task_id is not None:
            conditions.append("task_id = ?")
            params.append(filter_spec.task_id)
        if filter_spec.deliverable_doc_slug is not None:
            conditions.append("deliverable_doc_slug = ?")
            params.append(filter_spec.deliverable_doc_slug)
        return " AND ".join(conditions), params

    def _to_row(self, receipt: DeliverableReceipt) -> dict[str, object]:
        """Flatten a receipt into a row dict (full model in payload_json).

        Returns:
            Result of type ``dict[str, object]``.
        """
        return {
            "receipt_id": receipt.receipt_id,
            "task_id": receipt.task_id,
            "project_id": receipt.project_id,
            "execution_id": receipt.execution_id,
            "deliverable_doc_slug": receipt.deliverable_doc_slug,
            "issued_at": format_iso_utc(normalize_utc(receipt.issued_at)),
            "total_cost": receipt.total_cost,
            "currency": receipt.currency,
            "payload_json": receipt.model_dump_json(),
        }

    def _row_to_model(self, row: dict[str, object]) -> DeliverableReceipt:
        """Reconstruct a receipt from its stored JSON payload.

        Returns:
            Result of type ``DeliverableReceipt``.

        Raises:
            QueryError: If the payload cannot be deserialized.
        """
        payload = row.get("payload_json")
        try:
            return DeliverableReceipt.model_validate_json(str(payload))
        except ValidationError as exc:
            msg = "Failed to deserialize deliverable receipt payload"
            logger.warning(
                PERSISTENCE_RECEIPT_DESERIALIZE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
