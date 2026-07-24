"""SQLite repository implementation for in-flight approval resume intents."""

import contextlib
import sqlite3

import aiosqlite
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
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)


class SQLiteResumeIntentRepository:
    """SQLite implementation of the ResumeIntentRepository protocol.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager that serializes writes on
            the shared connection. Supplied by
            ``SQLitePersistenceBackend.write_context`` in production;
            tests can pass
            ``tests._shared.persistence.make_private_write_context()``
            for standalone construction.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def save(self, intent: ResumeIntent) -> None:
        """Record an in-flight resume intent, keeping any earlier one.

        ``INSERT OR IGNORE``, not a replace: when two callers race the
        same approval only one goes on to win ``save_if_pending``, and
        overwriting would let the loser's later timestamp mask the
        winner's genuinely in-flight marker from the startup drain.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(
                    "INSERT OR IGNORE INTO resume_intents "
                    "(approval_id, recorded_at) VALUES (:approval_id, :recorded_at)",
                    intent.model_dump(mode="json"),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
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
            async with self._db.execute(
                "SELECT approval_id, recorded_at FROM resume_intents "
                "WHERE approval_id = ?",
                (approval_id,),
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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

        return self._row_to_model(dict(row))

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
            async with self._db.execute(
                "SELECT approval_id, recorded_at FROM resume_intents "
                "ORDER BY approval_id LIMIT ? OFFSET ?",
                (limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list resume intents"
            logger.warning(
                PERSISTENCE_RESUME_INTENT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        results = tuple(self._row_to_model(dict(row)) for row in rows)
        logger.debug(PERSISTENCE_RESUME_INTENT_QUERIED, count=len(results))
        return results

    async def delete(self, approval_id: NotBlankStr) -> bool:
        """Clear the in-flight intent for an approval.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM resume_intents WHERE approval_id = ?",
                    (approval_id,),
                ) as cursor:
                    await self._db.commit()
                    deleted = cursor.rowcount > 0
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to delete resume intent {approval_id!r}"
                logger.warning(
                    PERSISTENCE_RESUME_INTENT_DELETE_FAILED,
                    approval_id=approval_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

        return deleted

    def _row_to_model(self, row: dict[str, object]) -> ResumeIntent:
        """Convert a database row to a ``ResumeIntent`` model.

        Raises:
            MalformedRowError: If the row cannot be deserialized.

        Returns:
            Result of type ``ResumeIntent``.
        """
        try:
            return ResumeIntent.model_validate(row)
        except (ValidationError, ValueError) as exc:
            msg = f"Failed to deserialize resume intent {row.get('approval_id')!r}"
            logger.warning(
                PERSISTENCE_RESUME_INTENT_DESERIALIZE_FAILED,
                approval_id=row.get("approval_id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise MalformedRowError(msg) from exc
