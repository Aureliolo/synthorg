# module-kind: repository
"""SQLite repository for measured per-model benchmark scores.

Satisfies ``BenchmarkScoreRepository`` structurally: id-keyed CRUD
(``save`` upsert / ``get`` / ``delete`` / ``list_items``) keyed by
``model_id``. There is no currency or state-machine surface here; a
benchmark score is a measured constant per model, re-recorded by the
offline scoring entry-point.
"""

import sqlite3

import aiosqlite
from aiosqlite import Row

from synthorg.budget.benchmark_models import BenchmarkScoreRecord
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.budget import (
    BUDGET_BENCHMARK_SCORE_FAILED,
    BUDGET_BENCHMARK_SCORE_FETCHED,
    BUDGET_BENCHMARK_SCORE_LISTED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000

_SELECT_COLS = (
    "model_id, score, confidence_lower, confidence_upper, "
    "source, suite_version, cassette_sha256, last_updated"
)

_UPSERT_SQL = f"""
    INSERT INTO benchmark_scores ({_SELECT_COLS})
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(model_id) DO UPDATE SET
        score = excluded.score,
        confidence_lower = excluded.confidence_lower,
        confidence_upper = excluded.confidence_upper,
        source = excluded.source,
        suite_version = excluded.suite_version,
        cassette_sha256 = excluded.cassette_sha256,
        last_updated = excluded.last_updated
"""  # noqa: S608 -- column list is a compile-time constant


async def _safe_rollback(
    db: aiosqlite.Connection,
    *,
    operation: str,
    **log_context: object,
) -> None:
    """Roll back the current transaction, logging any rollback failure."""
    try:
        await db.rollback()
    except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
        log_exception_redacted(
            logger,
            BUDGET_BENCHMARK_SCORE_FAILED,
            rollback_exc,
            phase="rollback",
            operation=operation,
            **log_context,
        )


def _row_to_record(row: Row) -> BenchmarkScoreRecord:
    """Convert a database row into a :class:`BenchmarkScoreRecord`.

    Returns:
        Result of type ``BenchmarkScoreRecord``.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        return BenchmarkScoreRecord(
            model_id=NotBlankStr(str(row["model_id"])),
            score=float(row["score"]),
            confidence_lower=float(row["confidence_lower"]),
            confidence_upper=float(row["confidence_upper"]),
            source=NotBlankStr(str(row["source"])),
            suite_version=NotBlankStr(str(row["suite_version"])),
            cassette_sha256=NotBlankStr(str(row["cassette_sha256"])),
            last_updated=coerce_row_timestamp(row["last_updated"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        msg = (
            f"Failed to parse benchmark score row: "
            f"{type(exc).__name__} ({safe_error_description(exc)})"
        )
        logger.warning(
            BUDGET_BENCHMARK_SCORE_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


class SQLiteBenchmarkScoreRepository:
    """SQLite-backed measured benchmark-score repository.

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

    async def save(self, entity: BenchmarkScoreRecord) -> None:
        """Upsert a benchmark-score row keyed by ``model_id``.

        Raises:
            ConstraintViolationError: On constraint violations (e.g. the
                confidence-band CHECK).
            QueryError: On other database errors.
        """
        params = (
            entity.model_id,
            float(entity.score),
            float(entity.confidence_lower),
            float(entity.confidence_upper),
            entity.source,
            entity.suite_version,
            entity.cassette_sha256,
            format_iso_utc(entity.last_updated),
        )
        async with self._write_context():
            try:
                await self._db.execute(_UPSERT_SQL, params)
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await _safe_rollback(
                    self._db, operation="save", model_id=entity.model_id
                )
                msg = (
                    f"Constraint violation saving benchmark score "
                    f"{entity.model_id!r}: {safe_error_description(exc)}"
                )
                logger.warning(
                    BUDGET_BENCHMARK_SCORE_FAILED,
                    operation="save",
                    model_id=entity.model_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise ConstraintViolationError(msg, constraint=str(exc)) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db, operation="save", model_id=entity.model_id
                )
                msg = (
                    f"Failed to save benchmark score {entity.model_id!r}: "
                    f"{type(exc).__name__} ({safe_error_description(exc)})"
                )
                logger.warning(
                    BUDGET_BENCHMARK_SCORE_FAILED,
                    operation="save",
                    model_id=entity.model_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> BenchmarkScoreRecord | None:
        """Get a benchmark score by ``model_id``, or ``None`` if not found.

        Returns:
            The matching record, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            f"SELECT {_SELECT_COLS} FROM benchmark_scores "  # noqa: S608
            "WHERE model_id = ?"
        )
        try:
            cursor = await self._db.execute(sql, (entity_id,))
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch benchmark score {entity_id!r}"
            logger.warning(
                BUDGET_BENCHMARK_SCORE_FAILED,
                operation="get",
                model_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        record = _row_to_record(row)
        logger.debug(BUDGET_BENCHMARK_SCORE_FETCHED, model_id=entity_id)
        return record

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[BenchmarkScoreRecord, ...]:
        """List scores ordered by ``model_id`` ascending (paginated).

        Returns:
            The matching records.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=BUDGET_BENCHMARK_SCORE_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        sql = (
            f"SELECT {_SELECT_COLS} FROM benchmark_scores "  # noqa: S608
            "ORDER BY model_id ASC LIMIT ? OFFSET ?"
        )
        try:
            cursor = await self._db.execute(sql, (effective_limit, offset))
            rows = await cursor.fetchall()
            items = tuple(_row_to_record(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list benchmark scores"
            logger.warning(
                BUDGET_BENCHMARK_SCORE_FAILED,
                operation="list_items",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(BUDGET_BENCHMARK_SCORE_LISTED, count=len(items))
        return items

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a benchmark score by ``model_id``.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM benchmark_scores WHERE model_id = ?"
        async with self._write_context():
            try:
                cursor = await self._db.execute(sql, (entity_id,))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(self._db, operation="delete", model_id=entity_id)
                msg = f"Failed to delete benchmark score {entity_id!r}"
                logger.warning(
                    BUDGET_BENCHMARK_SCORE_FAILED,
                    operation="delete",
                    model_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0


__all__ = ["SQLiteBenchmarkScoreRepository"]
