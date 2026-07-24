"""Postgres implementation of the ResumeIntentRepository protocol.

This is the Postgres sibling of
src/synthorg/persistence/sqlite/resume_intent_repo.py.
"""

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from synthorg.core.persistence_errors import MalformedRowError, QueryError
from synthorg.core.resume_intent import ResumeIntent
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.resume_intent import (
    PERSISTENCE_RESUME_INTENT_DELETE_FAILED,
    PERSISTENCE_RESUME_INTENT_DESERIALIZE_FAILED,
    PERSISTENCE_RESUME_INTENT_NOT_FOUND,
    PERSISTENCE_RESUME_INTENT_QUERIED,
    PERSISTENCE_RESUME_INTENT_QUERY_FAILED,
    PERSISTENCE_RESUME_INTENT_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args

logger = get_logger(__name__)


class PostgresResumeIntentRepository:
    """Postgres implementation of the ResumeIntentRepository protocol.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, intent: ResumeIntent) -> None:
        """Record an in-flight resume intent, keeping any earlier one.

        ``DO NOTHING``, not an upsert: when two callers race the same
        approval only one goes on to win ``save_if_pending``, and
        overwriting would let the loser's later timestamp mask the
        winner's genuinely in-flight marker from the startup drain.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO resume_intents (approval_id, recorded_at) "
                    "VALUES (%(approval_id)s, %(recorded_at)s) "
                    "ON CONFLICT(approval_id) DO NOTHING",
                    intent.model_dump(mode="json"),
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save resume intent {intent.approval_id!r}"
            logger.warning(
                PERSISTENCE_RESUME_INTENT_SAVE_FAILED,
                approval_id=intent.approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, approval_id: NotBlankStr) -> ResumeIntent | None:
        """Retrieve an in-flight intent by approval ID.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT approval_id, recorded_at FROM resume_intents "
                    "WHERE approval_id = %s",
                    (approval_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to query resume intent {approval_id!r}"
            logger.warning(
                PERSISTENCE_RESUME_INTENT_QUERY_FAILED,
                approval_id=approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            logger.debug(
                PERSISTENCE_RESUME_INTENT_NOT_FOUND,
                approval_id=approval_id,
            )
            return None

        return self._row_to_model(row)

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ResumeIntent, ...]:
        """List in-flight intents in approval-id order.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_RESUME_INTENT_QUERY_FAILED
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT approval_id, recorded_at FROM resume_intents "
                    "ORDER BY approval_id LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list resume intents"
            logger.warning(
                PERSISTENCE_RESUME_INTENT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        results = tuple(self._row_to_model(row) for row in rows)
        logger.debug(PERSISTENCE_RESUME_INTENT_QUERIED, count=len(results))
        return results

    async def delete(self, approval_id: NotBlankStr) -> bool:
        """Clear the in-flight intent for an approval.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM resume_intents WHERE approval_id = %s",
                    (approval_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete resume intent {approval_id!r}"
            logger.warning(
                PERSISTENCE_RESUME_INTENT_DELETE_FAILED,
                approval_id=approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        return deleted

    def _row_to_model(self, row: DictRow) -> ResumeIntent:
        """Convert a database row to a ``ResumeIntent`` model.

        Raises:
            MalformedRowError: If the row cannot be deserialized.

        Returns:
            Result of type ``ResumeIntent``.
        """
        try:
            return ResumeIntent.model_validate(dict(row))
        except (ValidationError, ValueError) as exc:
            msg = f"Failed to deserialize resume intent {row.get('approval_id')!r}"
            logger.warning(
                PERSISTENCE_RESUME_INTENT_DESERIALIZE_FAILED,
                approval_id=row.get("approval_id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise MalformedRowError(msg) from exc
