# module-kind: repository
"""SQLite repository for durable A/B-test rollout records.

Satisfies ``AbTestRepository`` structurally: id-keyed CRUD keyed by the
proposal id, with ``save`` as an upsert so a rollout that first writes a
``running`` record and later its terminal verdict replaces the same row.
The arm breakdown + verdict + elapsed observation hours live together in
the ``variants`` JSON column; ``list_items`` pages newest-first.
"""

import json
from typing import NoReturn

import aiosqlite

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
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_SELECT_COLS = "id, name, status, variants, created_at, updated_at"


def _encode_variants(record: AbTestRecord) -> str:
    """Serialise the arm breakdown + verdict + elapsed into JSON.

    Returns:
        The ``variants`` column payload as a JSON string.
    """
    payload = {
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
    return json.dumps(payload, separators=(",", ":"))


def _row_to_record(row: aiosqlite.Row) -> AbTestRecord:
    """Convert a database row into an :class:`AbTestRecord`.

    Returns:
        The reconstructed record.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        payload = json.loads(str(row["variants"]))
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
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        logger.warning(
            META_ABTEST_PERSISTENCE_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to parse A/B-test row: {type(exc).__name__}"
        raise QueryError(msg) from exc


class SQLiteAbTestRepository:
    """SQLite-backed durable A/B-test record store.

    Args:
        db: An open aiosqlite connection.
        write_context: Async write-serialising context manager.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._db.row_factory = aiosqlite.Row
        self._write_context = write_context

    async def save(self, entity: AbTestRecord) -> None:
        """Upsert an A/B-test record keyed by proposal id.

        Raises:
            QueryError: On database errors.
        """
        sql = """
            INSERT INTO ab_tests (id, name, status, variants, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                status = excluded.status,
                variants = excluded.variants,
                updated_at = excluded.updated_at
        """
        params = (
            entity.id,
            entity.name,
            entity.status.value,
            _encode_variants(entity),
            format_iso_utc(entity.created_at),
            format_iso_utc(entity.updated_at),
        )
        async with self._write_context():
            try:
                await self._db.execute(sql, params)
                await self._db.commit()
            except (aiosqlite.Error, ValueError) as exc:
                await self._rollback("save")
                self._raise_query_error("save A/B-test record", exc)

    async def get(self, entity_id: NotBlankStr) -> AbTestRecord | None:
        """Get a record by proposal id, or ``None`` when absent.

        Returns:
            The matching record, or ``None``.

        Raises:
            QueryError: If the database query fails.
        """
        sql = f"SELECT {_SELECT_COLS} FROM ab_tests WHERE id = ?"  # noqa: S608
        try:
            async with self._db.execute(sql, (entity_id,)) as cursor:
                row = await cursor.fetchone()
        except (aiosqlite.Error, ValueError) as exc:
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
            "ORDER BY created_at DESC, id ASC LIMIT ? OFFSET ?"
        )
        try:
            async with self._db.execute(sql, (effective_limit, offset)) as cursor:
                rows = await cursor.fetchall()
            return tuple(_row_to_record(r) for r in rows)
        except QueryError:
            raise
        except (aiosqlite.Error, ValueError) as exc:
            self._raise_query_error("list A/B-test records", exc)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a record by proposal id. ``True`` iff a row existed.

        Returns:
            ``True`` when a row was removed, ``False`` otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM ab_tests WHERE id = ?"
        async with self._write_context():
            try:
                async with self._db.execute(sql, (entity_id,)) as cursor:
                    await self._db.commit()
                    return cursor.rowcount > 0
            except (aiosqlite.Error, ValueError) as exc:
                await self._rollback("delete")
                self._raise_query_error("delete A/B-test record", exc)

    async def _rollback(self, operation: str) -> None:
        try:
            await self._db.rollback()
        except aiosqlite.Error as exc:
            logger.warning(
                META_ABTEST_PERSISTENCE_FAILED,
                operation=operation,
                phase="rollback",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    def _raise_query_error(self, operation: str, exc: Exception) -> NoReturn:
        logger.warning(
            META_ABTEST_PERSISTENCE_FAILED,
            operation=operation,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to {operation}: {type(exc).__name__}"
        raise QueryError(msg) from exc


__all__ = ["SQLiteAbTestRepository"]
