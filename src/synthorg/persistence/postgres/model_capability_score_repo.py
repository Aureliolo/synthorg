# module-kind: repository
"""Postgres repository for externally-sourced model capability scores.

Sibling of :class:`SQLiteModelCapabilityScoreRepository` backed by
``psycopg_pool.AsyncConnectionPool``. Satisfies
``ModelCapabilityScoreRepository`` structurally: composite-keyed CRUD on
``(source_label, model_identifier, axis)`` plus an all-or-nothing
``save_many`` used by feed ingest.
"""

from typing import Final, cast

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_CAPABILITY_SCORE_FAILED,
    PROVIDER_CAPABILITY_SCORE_FETCHED,
    PROVIDER_CAPABILITY_SCORE_LISTED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.providers.capability_sources.models import (
    CapabilityAxis,
    CapabilityScore,
    CapabilityScoreKey,
)

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: Final[int] = 5_000

_SELECT_COLS: Final[str] = (
    "source_label, model_identifier, axis, score, as_of, ingested_at"
)

_UPSERT_SQL = f"""
    INSERT INTO model_capability_scores ({_SELECT_COLS})
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (source_label, model_identifier, axis) DO UPDATE SET
        score = EXCLUDED.score,
        as_of = EXCLUDED.as_of,
        ingested_at = EXCLUDED.ingested_at
"""  # noqa: S608 -- column list is a compile-time constant

_KEY_WHERE: Final[str] = (
    "WHERE source_label = %s AND model_identifier = %s AND axis = %s"
)


def _params(entity: CapabilityScore) -> tuple[str, str, str, float, str, str]:
    """Return the positional bind parameters for one score row.

    Returns:
        The six column values in ``_SELECT_COLS`` order.
    """
    return (
        str(entity.source_label),
        str(entity.model_identifier),
        str(entity.axis),
        entity.score,
        format_iso_utc(entity.as_of),
        format_iso_utc(entity.ingested_at),
    )


def _row_to_score(row: DictRow) -> CapabilityScore:
    """Convert a Postgres dict row into a :class:`CapabilityScore`.

    Returns:
        The parsed :class:`CapabilityScore`.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        return CapabilityScore(
            source_label=NotBlankStr(str(row["source_label"])),
            model_identifier=NotBlankStr(str(row["model_identifier"])),
            axis=cast("CapabilityAxis", str(row["axis"])),
            score=float(row["score"]),
            as_of=coerce_row_timestamp(row["as_of"]),
            ingested_at=coerce_row_timestamp(row["ingested_at"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        error_type = type(exc).__name__
        error_desc = safe_error_description(exc)
        msg = f"Failed to parse capability score row: {error_type} ({error_desc})"
        logger.warning(
            PROVIDER_CAPABILITY_SCORE_FAILED,
            operation="deserialize",
            error_type=error_type,
            error=error_desc,
        )
        raise QueryError(msg) from exc


class PostgresModelCapabilityScoreRepository:
    """Postgres-backed capability-score repository.

    Args:
        pool: Async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: CapabilityScore) -> None:
        """Upsert one score by ``(source_label, model_identifier, axis)``.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        await self._write_rows((entity,), operation="save")

    async def save_many(self, entities: tuple[CapabilityScore, ...]) -> None:
        """Upsert a whole feed's scores in one transaction (all-or-nothing).

        An empty batch is a no-op. That is what a source which legitimately
        published nothing looks like, and it must not be mistaken for a
        reason to clear the source's existing rows.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        if not entities:
            return
        await self._write_rows(entities, operation="save_many")

    async def _write_rows(
        self,
        entities: tuple[CapabilityScore, ...],
        *,
        operation: str,
    ) -> None:
        """Upsert *entities* inside a single transaction.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        source_labels = sorted({str(e.source_label) for e in entities})
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.executemany(_UPSERT_SQL, [_params(e) for e in entities])
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            msg = (
                f"Constraint violation saving capability scores for "
                f"{source_labels}: {safe_error_description(exc)}"
            )
            logger.warning(
                PROVIDER_CAPABILITY_SCORE_FAILED,
                operation=operation,
                source_labels=source_labels,
                row_count=len(entities),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(msg, constraint=str(exc)) from exc
        except psycopg.Error as exc:
            msg = (
                f"Failed to save capability scores for {source_labels}: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
            logger.warning(
                PROVIDER_CAPABILITY_SCORE_FAILED,
                operation=operation,
                source_labels=source_labels,
                row_count=len(entities),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, entity_id: CapabilityScoreKey) -> CapabilityScore | None:
        """Get one score by composite key, or ``None`` when absent.

        Returns:
            The matching score, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            f"SELECT {_SELECT_COLS} FROM model_capability_scores "  # noqa: S608
            f"{_KEY_WHERE}"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, tuple(entity_id))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = (
                f"Failed to fetch capability score {entity_id!r}: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
            logger.warning(
                PROVIDER_CAPABILITY_SCORE_FAILED,
                operation="get",
                source_label=entity_id[0],
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        score = _row_to_score(row)
        logger.debug(
            PROVIDER_CAPABILITY_SCORE_FETCHED,
            source_label=entity_id[0],
            model_identifier=entity_id[1],
            axis=entity_id[2],
        )
        return score

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CapabilityScore, ...]:
        """List scores ordered by composite key ascending (paginated).

        Returns:
            The matching scores.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PROVIDER_CAPABILITY_SCORE_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        sql = (
            f"SELECT {_SELECT_COLS} FROM model_capability_scores "  # noqa: S608
            "ORDER BY source_label ASC, model_identifier ASC, axis ASC "
            "LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (effective_limit, offset))
                rows = await cur.fetchall()
            items = tuple(_row_to_score(r) for r in rows)
        except QueryError:
            raise
        except psycopg.Error as exc:
            msg = "Failed to list capability scores"
            logger.warning(
                PROVIDER_CAPABILITY_SCORE_FAILED,
                operation="list_items",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PROVIDER_CAPABILITY_SCORE_LISTED, count=len(items))
        return items

    async def delete(self, entity_id: CapabilityScoreKey) -> bool:
        """Delete one score by composite key.

        Returns:
            ``True`` when a row was deleted, ``False`` when none matched.

        Raises:
            QueryError: If the database query fails.
        """
        sql = f"DELETE FROM model_capability_scores {_KEY_WHERE}"  # noqa: S608
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, tuple(entity_id))
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = (
                f"Failed to delete capability score {entity_id!r}: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
            logger.warning(
                PROVIDER_CAPABILITY_SCORE_FAILED,
                operation="delete",
                source_label=entity_id[0],
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return rowcount > 0


__all__ = ["PostgresModelCapabilityScoreRepository"]
