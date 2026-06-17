"""SQLite repository for persisted upgrade recommendations.

Satisfies ``UpgradeRecommendationRepository`` structurally: id-keyed
CRUD, atomic status compare-and-set (``PENDING -> APPROVED``/
``REJECTED``/``AUTO_APPLIED`` carrying ``decided_at`` / ``decided_by``),
and filtered queries by ``status``.
"""

import json
import sqlite3
from datetime import datetime
from uuid import UUID

import aiosqlite

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.persistence.upgrade_recommendation import (
    PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
    PERSISTENCE_UPGRADE_RECOMMENDATION_FETCHED,
    PERSISTENCE_UPGRADE_RECOMMENDATION_LISTED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import format_iso_utc, validate_pagination_args
from synthorg.persistence._upgrade_recommendation_marshalling import (
    row_to_recommendation,
)
from synthorg.persistence.sqlite._shared import WriteContext
from synthorg.persistence.upgrade_recommendation_protocol import (
    UpgradeRecommendationFilterSpec,
)
from synthorg.providers.enums import RecommendationStatus
from synthorg.providers.management.upgrade_models import StoredUpgradeRecommendation

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000
_ALLOWED_TRANSITION_KEYS: frozenset[str] = frozenset({"decided_at", "decided_by"})

_SELECT_COLS = (
    "id, recommendation_json, agent_ids_json, status, "
    "created_at, decided_at, decided_by"
)

_UPSERT_SQL = f"""
    INSERT INTO upgrade_recommendations ({_SELECT_COLS})
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        recommendation_json = excluded.recommendation_json,
        agent_ids_json = excluded.agent_ids_json,
        status = excluded.status,
        created_at = excluded.created_at,
        decided_at = excluded.decided_at,
        decided_by = excluded.decided_by
"""  # noqa: S608  -- column list is a compile-time constant


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
            PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
            rollback_exc,
            phase="rollback",
            operation=operation,
            **log_context,
        )


class SQLiteUpgradeRecommendationRepository:
    """SQLite-backed upgrade-recommendation repository.

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

    async def save(self, entity: StoredUpgradeRecommendation) -> None:
        """Upsert a recommendation.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        params = (
            str(entity.id),
            entity.recommendation.model_dump_json(),
            json.dumps(list(entity.agent_ids)),
            entity.status.value,
            format_iso_utc(entity.created_at),
            format_iso_utc(entity.decided_at) if entity.decided_at else None,
            entity.decided_by,
        )
        async with self._write_context():
            try:
                await self._db.execute(_UPSERT_SQL, params)
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await _safe_rollback(self._db, operation="save", rec_id=str(entity.id))
                msg = f"Constraint violation saving recommendation {entity.id!r}"
                logger.warning(
                    PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
                    operation="save",
                    rec_id=str(entity.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise ConstraintViolationError(msg, constraint=str(exc)) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(self._db, operation="save", rec_id=str(entity.id))
                msg = f"Failed to save recommendation {entity.id!r}"
                logger.warning(
                    PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
                    operation="save",
                    rec_id=str(entity.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, entity_id: UUID) -> StoredUpgradeRecommendation | None:
        """Get a recommendation by id, or ``None`` if not found.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            f"SELECT {_SELECT_COLS} FROM upgrade_recommendations "  # noqa: S608
            "WHERE id = ?"
        )
        try:
            async with self._db.execute(sql, (str(entity_id),)) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch recommendation {entity_id!r}"
            logger.warning(
                PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
                operation="get",
                rec_id=str(entity_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        logger.debug(PERSISTENCE_UPGRADE_RECOMMENDATION_FETCHED, rec_id=str(entity_id))
        return row_to_recommendation(row)

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[StoredUpgradeRecommendation, ...]:
        """List recommendations newest-first (``created_at DESC, id DESC``).

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        effective_limit = min(
            validate_pagination_args(
                limit, offset, event=PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED
            ),
            _MAX_PAGE_LIMIT,
        )
        sql = (
            f"SELECT {_SELECT_COLS} FROM upgrade_recommendations "  # noqa: S608
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        )
        try:
            async with self._db.execute(sql, (effective_limit, offset)) as cursor:
                rows = await cursor.fetchall()
            items = tuple(row_to_recommendation(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list recommendations"
            logger.warning(
                PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
                operation="list_items",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_UPGRADE_RECOMMENDATION_LISTED, count=len(items))
        return items

    async def query(
        self,
        filter_spec: UpgradeRecommendationFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[StoredUpgradeRecommendation, ...]:
        """Return recommendations matching the spec, newest-first.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        effective_limit = min(
            validate_pagination_args(
                limit, offset, event=PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED
            ),
            _MAX_PAGE_LIMIT,
        )
        where, params = self._build_where(filter_spec)
        params.extend([effective_limit, offset])
        sql = f"""
            SELECT {_SELECT_COLS} FROM upgrade_recommendations
            WHERE {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
        """  # noqa: S608  -- ``where`` is a closed set of column predicates
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
            items = tuple(row_to_recommendation(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query recommendations"
            logger.warning(
                PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
                operation="query",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_UPGRADE_RECOMMENDATION_LISTED, count=len(items))
        return items

    async def count(self, filter_spec: UpgradeRecommendationFilterSpec) -> int:
        """Count recommendations matching the filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        where, params = self._build_where(filter_spec)
        sql = (
            "SELECT COUNT(*) FROM upgrade_recommendations "  # noqa: S608
            f"WHERE {where}"
        )
        try:
            async with self._db.execute(sql, params) as cursor:
                row = await cursor.fetchone()
            assert row is not None  # noqa: S101  -- COUNT always returns a row
            return int(row[0])
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to count recommendations"
            logger.warning(
                PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
                operation="count",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def transition_if(
        self,
        /,
        entity_id: UUID,
        from_state: RecommendationStatus,
        to_state: RecommendationStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for the recommendation status.

        Returns:
            ``True`` iff the row was in ``from_state`` and is now in
            ``to_state``; ``False`` on mismatch or missing row.

        Raises:
            QueryError: On database errors or an unknown ``updates`` key.
        """
        extra = set(updates) - _ALLOWED_TRANSITION_KEYS
        if extra:
            # Programmer-error guard, not a persistence fault: raise
            # directly rather than emitting a noisy persistence-failed
            # warning for what is a caller bug.
            msg = f"transition_if got unknown update keys {sorted(extra)!r}"
            raise QueryError(msg)

        set_cols = ["status = ?"]
        set_params: list[object] = [to_state.value]
        if "decided_at" in updates:
            decided_at = updates["decided_at"]
            set_cols.append("decided_at = ?")
            set_params.append(
                format_iso_utc(decided_at)
                if isinstance(decided_at, datetime)
                else decided_at,
            )
        if "decided_by" in updates:
            set_cols.append("decided_by = ?")
            set_params.append(updates["decided_by"])

        sql = (
            f"UPDATE upgrade_recommendations SET {', '.join(set_cols)} "  # noqa: S608
            "WHERE id = ? AND status = ?"
        )
        params = (*set_params, str(entity_id), from_state.value)
        async with self._write_context():
            try:
                async with self._db.execute(sql, params) as cursor:
                    await self._db.commit()
                    rowcount = cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db, operation="transition_if", rec_id=str(entity_id)
                )
                msg = f"Failed to transition recommendation {entity_id!r}"
                logger.warning(
                    PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
                    operation="transition_if",
                    rec_id=str(entity_id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return rowcount > 0

    async def delete(self, entity_id: UUID) -> bool:
        """Delete a recommendation by id. ``True`` iff a row existed.

        Returns:
            ``True`` when a row was deleted, ``False`` otherwise.

        Raises:
            QueryError: If the database operation fails.
        """
        sql = "DELETE FROM upgrade_recommendations WHERE id = ?"
        async with self._write_context():
            try:
                async with self._db.execute(sql, (str(entity_id),)) as cursor:
                    await self._db.commit()
                    rowcount = cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db, operation="delete", rec_id=str(entity_id)
                )
                msg = f"Failed to delete recommendation {entity_id!r}"
                logger.warning(
                    PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
                    operation="delete",
                    rec_id=str(entity_id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return rowcount > 0

    @staticmethod
    def _build_where(
        filter_spec: UpgradeRecommendationFilterSpec,
    ) -> tuple[str, list[object]]:
        """Build the WHERE clause + bound params from a filter spec.

        Returns:
            ``(where_clause, params)`` (clause excludes the leading WHERE).
        """
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.status is not None:
            clauses.append("status = ?")
            params.append(filter_spec.status.value)
        where = " AND ".join(clauses) if clauses else "1=1"
        return where, params


__all__ = ["SQLiteUpgradeRecommendationRepository"]
