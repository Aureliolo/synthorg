# module-kind: repository
"""Postgres implementation of the ``DeliverableReceiptRepository`` protocol.

Postgres sibling of ``persistence/sqlite/deliverable_receipt_repo.py``.
``issued_at`` is stored as TIMESTAMPTZ; the full receipt is stored as
TEXT in ``payload_json``. ``save`` upserts on the UNIQUE ``task_id``.
"""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
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
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.deliverable_receipt_protocol import (
    DeliverableReceiptFilterSpec,
)

logger = get_logger(__name__)

_COLUMNS = (
    "receipt_id, task_id, project_id, execution_id, deliverable_doc_slug, "
    "issued_at, total_cost, currency, payload_json"
)

_UPSERT_SQL = f"""\
INSERT INTO deliverable_receipt ({_COLUMNS}) VALUES (
    %(receipt_id)s, %(task_id)s, %(project_id)s, %(execution_id)s,
    %(deliverable_doc_slug)s, %(issued_at)s, %(total_cost)s, %(currency)s,
    %(payload_json)s
)
ON CONFLICT (task_id) DO UPDATE SET
    receipt_id = EXCLUDED.receipt_id,
    project_id = EXCLUDED.project_id,
    execution_id = EXCLUDED.execution_id,
    deliverable_doc_slug = EXCLUDED.deliverable_doc_slug,
    issued_at = EXCLUDED.issued_at,
    total_cost = EXCLUDED.total_cost,
    currency = EXCLUDED.currency,
    payload_json = EXCLUDED.payload_json
"""


class PostgresDeliverableReceiptRepository:
    """Postgres implementation of ``DeliverableReceiptRepository``.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: DeliverableReceipt) -> None:
        """Persist a receipt via upsert keyed on ``task_id``.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_UPSERT_SQL, self._to_row(entity))
                await conn.commit()
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT payload_json FROM deliverable_receipt "
                    "WHERE receipt_id = %s",
                    (entity_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        return self._row_to_model(row)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a receipt by ``receipt_id``.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM deliverable_receipt WHERE receipt_id = %s",
                    (entity_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT payload_json FROM deliverable_receipt "
                    "ORDER BY issued_at DESC, receipt_id DESC LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list deliverable receipts"
            logger.warning(
                PERSISTENCE_RECEIPT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_model(r) for r in rows)

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
            "ORDER BY issued_at DESC, receipt_id DESC LIMIT %s OFFSET %s"
        )
        all_params = [*params, limit, offset]
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, all_params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query deliverable receipts"
            logger.warning(
                PERSISTENCE_RECEIPT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_model(r) for r in rows)

    async def count(self, filter_spec: DeliverableReceiptFilterSpec) -> int:
        """Count receipts matching the filter spec.

        Returns:
            Number of matching receipts.

        Raises:
            QueryError: If the database query fails.
        """
        where, params = self._build_where(filter_spec)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT COUNT(*) AS n FROM deliverable_receipt WHERE {where}",
                    params,
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = "Failed to count deliverable receipts"
            logger.warning(
                PERSISTENCE_RECEIPT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(row["n"]) if row is not None else 0

    def _build_where(
        self, filter_spec: DeliverableReceiptFilterSpec
    ) -> tuple[str, list[object]]:
        """Build the WHERE clause + positional params for ``filter_spec``.

        Returns:
            ``(where_clause, params)`` without the leading ``WHERE``.
        """
        conditions: list[str] = ["project_id = %s"]
        params: list[object] = [filter_spec.project_id]
        if filter_spec.task_id is not None:
            conditions.append("task_id = %s")
            params.append(filter_spec.task_id)
        if filter_spec.deliverable_doc_slug is not None:
            conditions.append("deliverable_doc_slug = %s")
            params.append(filter_spec.deliverable_doc_slug)
        return " AND ".join(conditions), params

    def _to_row(self, receipt: DeliverableReceipt) -> dict[str, object]:
        """Flatten a receipt into a row dict (full model in payload_json).

        The payload is serialised from a UTC-normalised copy so the
        ``issued_at`` inside ``payload_json`` matches the normalised
        value stored in the indexed column (a round-trip read otherwise
        returns a timezone that diverges from what queries filter on).

        Returns:
            Result of type ``dict[str, object]``.
        """
        normalised = receipt.model_copy(
            update={"issued_at": normalize_utc(receipt.issued_at)}
        )
        return {
            "receipt_id": normalised.receipt_id,
            "task_id": normalised.task_id,
            "project_id": normalised.project_id,
            "execution_id": normalised.execution_id,
            "deliverable_doc_slug": normalised.deliverable_doc_slug,
            "issued_at": normalised.issued_at,
            "total_cost": normalised.total_cost,
            "currency": normalised.currency,
            "payload_json": normalised.model_dump_json(),
        }

    def _row_to_model(self, row: DictRow) -> DeliverableReceipt:
        """Reconstruct a receipt from its stored JSON payload.

        Returns:
            Result of type ``DeliverableReceipt``.

        Raises:
            QueryError: If the payload cannot be deserialized.
        """
        try:
            return DeliverableReceipt.model_validate_json(str(row["payload_json"]))
        except ValidationError as exc:
            msg = "Failed to deserialize deliverable receipt payload"
            logger.warning(
                PERSISTENCE_RECEIPT_DESERIALIZE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
