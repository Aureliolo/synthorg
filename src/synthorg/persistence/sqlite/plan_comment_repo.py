"""SQLite repository implementation for PlanItemComment."""

import sqlite3
from datetime import datetime

import aiosqlite
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
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    normalize_utc,
)
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.plan_comment_protocol import PlanItemCommentFilterSpec
from synthorg.persistence.sqlite._shared import (
    WriteContext,
    is_unique_constraint_error,
)

logger = get_logger(__name__)

_COLUMNS = (
    "id, plan_id, item_id, author, author_kind, author_agent_id, "
    "reply_to_id, body, created_at"
)


def _row_to_comment(row: aiosqlite.Row) -> PlanItemComment:
    """Reconstruct a ``PlanItemComment`` from a database row.

    Returns:
        Validated ``PlanItemComment`` model instance.
    """
    data = dict(row)
    data["created_at"] = coerce_row_timestamp(data["created_at"])
    return PlanItemComment.model_validate(data)


class SQLitePlanItemCommentRepository:
    """SQLite-backed plan-item comment repository.

    Args:
        db: An open aiosqlite connection with ``row_factory`` set to
            ``aiosqlite.Row``.
        write_context: Async context manager that serializes writes on the
            shared connection.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def append(self, event: PlanItemComment, /) -> None:
        """Append a comment, failing if the id already exists.

        Raises:
            DuplicateRecordError: A comment with the same id exists.
            QueryError: If the database operation fails.
        """
        params = (
            str(event.id),
            event.plan_id,
            event.item_id,
            event.author,
            event.author_kind,
            event.author_agent_id,
            str(event.reply_to_id) if event.reply_to_id is not None else None,
            event.body,
            format_iso_utc(event.created_at),
        )
        async with self._write_context():
            try:
                await self._db.execute(
                    f"INSERT INTO plan_item_comments ({_COLUMNS}) "  # noqa: S608
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    params,
                )
                await self._db.commit()
            except (sqlite3.IntegrityError, aiosqlite.IntegrityError) as exc:
                await self._safe_rollback()
                logger.warning(
                    PERSISTENCE_PLAN_COMMENT_SAVE_FAILED,
                    plan_id=event.plan_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                if is_unique_constraint_error(exc):
                    msg = f"Comment with id {event.id!r} already exists"
                    raise DuplicateRecordError(msg) from exc
                msg = f"Failed to append comment {event.id!r}"
                raise QueryError(msg) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
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
        where = "plan_id = ?"
        params: list[object] = [filter_spec.plan_id]
        if filter_spec.item_id is not None:
            where += " AND item_id = ?"
            params.append(filter_spec.item_id)
        params.extend((limit, offset))
        try:
            async with self._db.execute(
                f"SELECT {_COLUMNS} FROM plan_item_comments "  # noqa: S608
                f"WHERE {where} ORDER BY created_at ASC, id ASC LIMIT ? OFFSET ?",
                params,
            ) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        except (ValueError, ValidationError, KeyError, IndexError, TypeError) as exc:
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
        cutoff = format_iso_utc(normalize_utc(threshold))
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM plan_item_comments WHERE created_at < ?",
                    (cutoff,),
                ) as cursor:
                    removed = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                logger.warning(
                    PERSISTENCE_PLAN_COMMENT_PURGE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = "Failed to purge plan comments"
                raise QueryError(msg) from exc
        return removed

    async def _safe_rollback(self) -> None:
        """Best-effort rollback on the shared connection."""
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
            logger.warning(
                PERSISTENCE_PLAN_COMMENT_SAVE_FAILED,
                error_type=type(rollback_exc).__name__,
                rollback_failed=True,
            )
