# module-kind: repository
"""SQLite repository implementation for lifecycle transitions."""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

import contextlib
import sqlite3
from datetime import datetime

import aiosqlite
from pydantic import ValidationError

from synthorg.core.lifecycle_transition import LifecycleTransition
from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.lifecycle_transition import (
    PERSISTENCE_LIFECYCLE_TRANSITION_QUERY_FAILED,
    PERSISTENCE_LIFECYCLE_TRANSITION_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared.datetime_marshaller import format_iso_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.lifecycle_transition_protocol import (
    LifecycleTransitionFilterSpec,
)
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_COLUMNS = (
    "id, entity_kind, entity_id, from_status, to_status, requested_by, "
    "reason, entity_version, occurred_at"
)

_INSERT_SQL = f"""\
INSERT INTO lifecycle_transitions ({_COLUMNS}) VALUES (
    :id, :entity_kind, :entity_id, :from_status, :to_status, :requested_by,
    :reason, :entity_version, :occurred_at
)"""


class SQLiteLifecycleTransitionRepository:
    """SQLite implementation of ``LifecycleTransitionRepository``.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager that serializes writes on
            the shared connection.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def append(self, event: LifecycleTransition) -> None:
        """Persist one transition (append-only).

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(_INSERT_SQL, _to_row(event))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to record transition for {event.entity_id!r}"
                logger.warning(
                    PERSISTENCE_LIFECYCLE_TRANSITION_SAVE_FAILED,
                    entity_kind=event.entity_kind.value,
                    entity_id=event.entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: LifecycleTransitionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[LifecycleTransition, ...]:
        """Return transitions matching the filter, newest-first.

        Returns:
            The matching transitions.

        Raises:
            QueryError: If the database query fails or a row is malformed.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_LIFECYCLE_TRANSITION_QUERY_FAILED
        )
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.entity_kind is not None:
            clauses.append("entity_kind = ?")
            params.append(filter_spec.entity_kind.value)
        if filter_spec.entity_id is not None:
            clauses.append("entity_id = ?")
            params.append(filter_spec.entity_id)
        where = " AND ".join(clauses) if clauses else "1=1"
        sql = (
            f"SELECT {_COLUMNS} FROM lifecycle_transitions WHERE {where} "
            "ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
            return tuple(LifecycleTransition.model_validate(dict(r)) for r in rows)
        except (sqlite3.Error, aiosqlite.Error, ValidationError) as exc:
            msg = "Failed to query lifecycle transitions"
            logger.warning(
                PERSISTENCE_LIFECYCLE_TRANSITION_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def purge_before(self, threshold: datetime) -> int:
        """Delete transitions with ``occurred_at < threshold``.

        Args:
            threshold: Timezone-aware UTC timestamp. A naive datetime is
                rejected to prevent silent local-time misinterpretation
                deleting the wrong retention window.

        Returns:
            Number of rows deleted.

        Raises:
            QueryError: If *threshold* is naive or the query fails.
        """
        if threshold.tzinfo is None:
            msg = "threshold must be timezone-aware; a naive datetime is rejected"
            raise QueryError(msg)
        aware = normalize_utc(threshold)
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM lifecycle_transitions WHERE occurred_at < ?",
                    (format_iso_utc(aware),),
                ) as cursor:
                    count = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = "Failed to purge lifecycle transitions by threshold"
                logger.warning(
                    PERSISTENCE_LIFECYCLE_TRANSITION_QUERY_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return count


def _to_row(event: LifecycleTransition) -> dict[str, object]:
    """Flatten a transition into a row dict.

    Returns:
        The row as bound parameters.
    """
    data = event.model_dump(mode="json")
    data["occurred_at"] = format_iso_utc(normalize_utc(event.occurred_at))
    return data


__all__ = ["SQLiteLifecycleTransitionRepository"]
