"""Postgres repository for persisted upgrade recommendations.

Sibling of ``SQLiteUpgradeRecommendationRepository`` backed by
``psycopg_pool.AsyncConnectionPool``. Satisfies
``UpgradeRecommendationRepository`` structurally.
"""

import json
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.upgrade_recommendation import (
    PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
    PERSISTENCE_UPGRADE_RECOMMENDATION_FETCHED,
    PERSISTENCE_UPGRADE_RECOMMENDATION_LISTED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence._upgrade_recommendation_marshalling import (
    row_to_recommendation,
)
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
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        recommendation_json = EXCLUDED.recommendation_json,
        agent_ids_json = EXCLUDED.agent_ids_json,
        status = EXCLUDED.status,
        created_at = EXCLUDED.created_at,
        decided_at = EXCLUDED.decided_at,
        decided_by = EXCLUDED.decided_by
"""  # noqa: S608  -- column list is a compile-time constant


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
        clauses.append("status = %s")
        params.append(filter_spec.status.value)
    where = " AND ".join(clauses) if clauses else "TRUE"
    return where, params


class PostgresUpgradeRecommendationRepository:
    """Postgres-backed upgrade-recommendation repository.

    Args:
        pool: An open psycopg async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

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
            entity.created_at,
            entity.decided_at,
            entity.decided_by,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_UPSERT_SQL, params)
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            constraint = (
                getattr(getattr(exc, "diag", None), "constraint_name", None)
                or "<unknown>"
            )
            msg = f"Constraint violation saving recommendation {entity.id!r}"
            logger.warning(
                PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
                operation="save",
                rec_id=str(entity.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(msg, constraint=constraint) from exc
        except psycopg.Error as exc:
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
            "WHERE id = %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (str(entity_id),))
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLS} "  # noqa: S608
                    "FROM upgrade_recommendations "
                    "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                    (effective_limit, offset),
                )
                rows = await cur.fetchall()
                items = tuple(row_to_recommendation(r) for r in rows)
        except QueryError:
            raise
        except psycopg.Error as exc:
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
        where, params = _build_where(filter_spec)
        params.extend([effective_limit, offset])
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLS} "  # noqa: S608
                    f"FROM upgrade_recommendations WHERE {where} "
                    "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                    params,
                )
                rows = await cur.fetchall()
                items = tuple(row_to_recommendation(r) for r in rows)
        except QueryError:
            raise
        except psycopg.Error as exc:
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
        where, params = _build_where(filter_spec)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) FROM upgrade_recommendations "  # noqa: S608
                    f"WHERE {where}",
                    params,
                )
                row = await cur.fetchone()
                assert row is not None  # noqa: S101  -- COUNT always returns a row
                return int(row[0])
        except psycopg.Error as exc:
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

        set_cols = ["status = %s"]
        set_params: list[object] = [to_state.value]
        if "decided_at" in updates:
            set_cols.append("decided_at = %s")
            set_params.append(updates["decided_at"])
        if "decided_by" in updates:
            set_cols.append("decided_by = %s")
            set_params.append(updates["decided_by"])

        sql = (
            f"UPDATE upgrade_recommendations SET {', '.join(set_cols)} "  # noqa: S608
            "WHERE id = %s AND status = %s"
        )
        params = (*set_params, str(entity_id), from_state.value)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                updated = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to transition recommendation {entity_id!r}"
            logger.warning(
                PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
                operation="transition_if",
                rec_id=str(entity_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return updated

    async def delete(self, entity_id: UUID) -> bool:
        """Delete a recommendation by id. ``True`` iff a row existed.

        Returns:
            ``True`` when a row was deleted, ``False`` otherwise.

        Raises:
            QueryError: If the database operation fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM upgrade_recommendations WHERE id = %s",
                    (str(entity_id),),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete recommendation {entity_id!r}"
            logger.warning(
                PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
                operation="delete",
                rec_id=str(entity_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted


__all__ = ["PostgresUpgradeRecommendationRepository"]
