"""Postgres repository implementation for PlanItemComment."""

from datetime import datetime

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from synthorg.core.persistence_errors import (
    DuplicateRecordError,
    QueryError,
)
from synthorg.core.plan_comment import PlanItemComment
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.plan import (
    PERSISTENCE_PLAN_COMMENT_DESERIALIZE_FAILED,
    PERSISTENCE_PLAN_COMMENT_LIST_FAILED,
    PERSISTENCE_PLAN_COMMENT_PURGE_FAILED,
    PERSISTENCE_PLAN_COMMENT_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import coerce_row_timestamp, normalize_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.plan_comment_protocol import PlanItemCommentFilterSpec

logger = get_logger(__name__)

_COLUMNS = (
    "id, plan_id, item_id, author, author_kind, author_agent_id, "
    "reply_to_id, body, created_at"
)


def _row_to_comment(row: DictRow) -> PlanItemComment:
    """Reconstruct a ``PlanItemComment`` from a Postgres dict_row.

    Returns:
        Validated ``PlanItemComment`` model instance.
    """
    row["created_at"] = coerce_row_timestamp(row["created_at"])
    return PlanItemComment.model_validate(row)


class PostgresPlanItemCommentRepository:
    """Postgres-backed plan-item comment repository.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, event: PlanItemComment, /) -> None:
        """Append a comment, failing if the id already exists.

        Raises:
            DuplicateRecordError: A comment with the same id exists.
            QueryError: If the database operation fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    f"INSERT INTO plan_item_comments ({_COLUMNS}) "  # noqa: S608
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        str(event.id),
                        event.plan_id,
                        event.item_id,
                        event.author,
                        event.author_kind,
                        event.author_agent_id,
                        str(event.reply_to_id)
                        if event.reply_to_id is not None
                        else None,
                        event.body,
                        event.created_at,
                    ),
                )
                await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            logger.warning(
                PERSISTENCE_PLAN_COMMENT_SAVE_FAILED,
                plan_id=event.plan_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Comment with id {event.id!r} already exists"
            raise DuplicateRecordError(msg) from exc
        except psycopg.Error as exc:
            logger.warning(
                PERSISTENCE_PLAN_COMMENT_SAVE_FAILED,
                plan_id=event.plan_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to append comment {event.id!r}"
            raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: PlanItemCommentFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[PlanItemComment, ...]:
        """Return a plan's comments oldest-first (see the protocol).

        Returns:
            Matching comments ordered ``(created_at ASC, id ASC)``.

        Raises:
            QueryError: If the operation fails or pagination args are invalid.
        """
        limit = validate_pagination_args(
            limit,
            offset,
            event=PERSISTENCE_PLAN_COMMENT_LIST_FAILED,
            plan_id=filter_spec.plan_id,
        )
        where = "plan_id = %s"
        params: list[object] = [filter_spec.plan_id]
        if filter_spec.item_id is not None:
            where += " AND item_id = %s"
            params.append(filter_spec.item_id)
        if filter_spec.comment_id is not None:
            where += " AND id = %s"
            params.append(str(filter_spec.comment_id))
        params.extend((limit, offset))
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_COLUMNS} FROM plan_item_comments "  # noqa: S608
                    f"WHERE {where} ORDER BY created_at ASC, id ASC "
                    "LIMIT %s OFFSET %s",
                    params,
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            logger.warning(
                PERSISTENCE_PLAN_COMMENT_LIST_FAILED,
                plan_id=filter_spec.plan_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to list plan comments"
            raise QueryError(msg) from exc
        try:
            return tuple(_row_to_comment(row) for row in rows)
        except (ValueError, ValidationError, KeyError) as exc:
            logger.warning(
                PERSISTENCE_PLAN_COMMENT_DESERIALIZE_FAILED,
                plan_id=filter_spec.plan_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to deserialize plan comment row"
            raise QueryError(msg) from exc

    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete comments older than ``threshold``.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the operation fails.
        """
        if threshold.tzinfo is None:
            msg = "purge_before threshold must be timezone-aware"
            raise QueryError(msg)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM plan_item_comments WHERE created_at < %s",
                    (normalize_utc(threshold),),
                )
                removed = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            logger.warning(
                PERSISTENCE_PLAN_COMMENT_PURGE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to purge plan comments"
            raise QueryError(msg) from exc
        return removed
