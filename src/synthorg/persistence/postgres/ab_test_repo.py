# module-kind: repository
"""Postgres repository for durable A/B-test rollout records.

Sibling of :class:`SQLiteAbTestRepository` backed by
``psycopg_pool.AsyncConnectionPool``. Id-keyed CRUD keyed by the
proposal id; ``save`` upserts so a running record is replaced by its
terminal verdict. The arm breakdown + verdict + elapsed live in the
``variants`` JSONB column; ``list_items`` pages newest-first.
"""

import json
from typing import NoReturn

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.meta.rollout.ab_models import (
    AbTestArm,
    AbTestRecord,
    AbTestStatus,
    ABTestVerdict,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import META_ABTEST_PERSISTENCE_FAILED
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)

logger = get_logger(__name__)

_SELECT_COLS = "id, name, status, variants, created_at, updated_at"


def _encode_variants(record: AbTestRecord) -> dict[str, object]:
    """Build the ``variants`` JSONB payload from a record.

    Returns:
        A JSON-serialisable dict for the ``variants`` column.
    """
    return {
        "arms": [
            {
                "name": str(arm.name),
                "agent_count": arm.agent_count,
                "fraction": arm.fraction,
            }
            for arm in record.arms
        ],
        "verdict": record.verdict.value if record.verdict is not None else None,
        "observation_hours_elapsed": record.observation_hours_elapsed,
    }


def _row_to_record(row: DictRow) -> AbTestRecord:
    """Convert a database row into an :class:`AbTestRecord`.

    Returns:
        The reconstructed record.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        raw = row["variants"]
        # A corrupt ``variants`` cell can decode to a list/str/number; the
        # ``.get()`` calls below then raise AttributeError, which is caught
        # alongside the parse errors and wrapped as QueryError.
        payload = json.loads(raw) if isinstance(raw, str) else raw
        arms = tuple(
            AbTestArm(
                name=NotBlankStr(str(arm["name"])),
                agent_count=int(arm["agent_count"]),
                fraction=float(arm["fraction"]),
            )
            for arm in payload.get("arms", ())
        )
        raw_verdict = payload.get("verdict")
        return AbTestRecord(
            id=NotBlankStr(str(row["id"])),
            name=NotBlankStr(str(row["name"])),
            status=AbTestStatus(str(row["status"])),
            arms=arms,
            verdict=ABTestVerdict(raw_verdict) if raw_verdict is not None else None,
            observation_hours_elapsed=float(
                payload.get("observation_hours_elapsed", 0.0)
            ),
            created_at=coerce_row_timestamp(row["created_at"]),
            updated_at=coerce_row_timestamp(row["updated_at"]),
        )
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        json.JSONDecodeError,
    ) as exc:
        logger.warning(
            META_ABTEST_PERSISTENCE_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to parse A/B-test row: {type(exc).__name__}"
        raise QueryError(msg) from exc


class PostgresAbTestRepository:
    """Postgres-backed durable A/B-test record store.

    Args:
        pool: Async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: AbTestRecord) -> None:
        """Upsert an A/B-test record keyed by proposal id.

        Raises:
            QueryError: On database errors.
        """
        sql = """
            INSERT INTO ab_tests (id, name, status, variants, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                status = EXCLUDED.status,
                variants = EXCLUDED.variants,
                updated_at = EXCLUDED.updated_at
        """
        params = (
            entity.id,
            entity.name,
            entity.status.value,
            Jsonb(_encode_variants(entity)),
            format_iso_utc(entity.created_at),
            format_iso_utc(entity.updated_at),
        )
        try:
            async with self._pool.connection() as conn:
                await conn.execute(sql, params)
                await conn.commit()
        except psycopg.Error as exc:
            self._raise_query_error("save A/B-test record", exc)

    async def get(self, entity_id: NotBlankStr) -> AbTestRecord | None:
        """Get a record by proposal id, or ``None`` when absent.

        Returns:
            The matching record, or ``None``.

        Raises:
            QueryError: If the database query fails.
        """
        sql = f"SELECT {_SELECT_COLS} FROM ab_tests WHERE id = %s"  # noqa: S608
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (entity_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            self._raise_query_error("get A/B-test record", exc)
        return None if row is None else _row_to_record(row)

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[AbTestRecord, ...]:
        """List records newest-first by ``created_at`` (paginated).

        Returns:
            The matching records, newest-first.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=META_ABTEST_PERSISTENCE_FAILED
        )
        sql = (
            f"SELECT {_SELECT_COLS} FROM ab_tests "  # noqa: S608
            "ORDER BY created_at DESC, id ASC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (effective_limit, offset))
                rows = await cur.fetchall()
            return tuple(_row_to_record(r) for r in rows)
        except psycopg.Error as exc:
            self._raise_query_error("list A/B-test records", exc)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a record by proposal id. ``True`` iff a row existed.

        Returns:
            ``True`` when a row was removed, ``False`` otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM ab_tests WHERE id = %s"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, (entity_id,))
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            self._raise_query_error("delete A/B-test record", exc)
        return rowcount > 0

    def _raise_query_error(self, operation: str, exc: Exception) -> NoReturn:
        logger.warning(
            META_ABTEST_PERSISTENCE_FAILED,
            operation=operation,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to {operation}: {type(exc).__name__}"
        raise QueryError(msg) from exc


__all__ = ["PostgresAbTestRepository"]
