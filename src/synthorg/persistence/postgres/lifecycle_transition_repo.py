# module-kind: repository
"""Postgres implementation of the ``LifecycleTransitionRepository`` protocol.

Postgres sibling of ``persistence/sqlite/lifecycle_transition_repo.py``.
``occurred_at`` is stored as TIMESTAMPTZ.
"""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

from datetime import datetime
from typing import Final, LiteralString

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from synthorg.core.lifecycle_transition import LifecycleTransition
from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.lifecycle_transition import (
    PERSISTENCE_LIFECYCLE_TRANSITION_APPEND_FAILED,
    PERSISTENCE_LIFECYCLE_TRANSITION_APPENDED,
    PERSISTENCE_LIFECYCLE_TRANSITION_DESERIALIZE_FAILED,
    PERSISTENCE_LIFECYCLE_TRANSITION_PURGE_FAILED,
    PERSISTENCE_LIFECYCLE_TRANSITION_QUERIED,
    PERSISTENCE_LIFECYCLE_TRANSITION_QUERY_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.lifecycle_transition_protocol import (
    LifecycleTransitionFilterSpec,
)

logger = get_logger(__name__)

_COLUMNS: Final[LiteralString] = (
    "id, entity_kind, entity_id, from_status, to_status, requested_by, "
    "reason, entity_version, occurred_at"
)

_INSERT_SQL: Final[LiteralString] = f"""\
INSERT INTO lifecycle_transitions ({_COLUMNS}) VALUES (
    %(id)s, %(entity_kind)s, %(entity_id)s, %(from_status)s, %(to_status)s,
    %(requested_by)s, %(reason)s, %(entity_version)s, %(occurred_at)s
)"""


class PostgresLifecycleTransitionRepository:
    """Postgres implementation of ``LifecycleTransitionRepository``.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, event: LifecycleTransition) -> None:
        """Persist one transition (append-only).

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_INSERT_SQL, _to_row(event))
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to record transition for {event.entity_id!r}"
            logger.warning(
                PERSISTENCE_LIFECYCLE_TRANSITION_APPEND_FAILED,
                entity_kind=event.entity_kind.value,
                entity_id=event.entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_LIFECYCLE_TRANSITION_APPENDED,
            entity_kind=event.entity_kind.value,
            entity_id=event.entity_id,
            to_status=event.to_status,
        )

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
        clauses: list[LiteralString] = []
        params: list[object] = []
        if filter_spec.entity_kind is not None:
            clauses.append("entity_kind = %s")
            params.append(filter_spec.entity_kind.value)
        if filter_spec.entity_id is not None:
            clauses.append("entity_id = %s")
            params.append(filter_spec.entity_id)
        where: LiteralString = " AND ".join(clauses) if clauses else "TRUE"
        sql: LiteralString = (
            f"SELECT {_COLUMNS} FROM lifecycle_transitions WHERE {where} "
            "ORDER BY occurred_at DESC, id DESC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, [*params, limit, offset])
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query lifecycle transitions"
            logger.warning(
                PERSISTENCE_LIFECYCLE_TRANSITION_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        transitions = tuple(_row_to_model(r) for r in rows)
        logger.debug(PERSISTENCE_LIFECYCLE_TRANSITION_QUERIED, count=len(transitions))
        return transitions

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
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM lifecycle_transitions WHERE occurred_at < %s",
                    (normalize_utc(threshold),),
                )
                count = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge lifecycle transitions by threshold"
            logger.warning(
                PERSISTENCE_LIFECYCLE_TRANSITION_PURGE_FAILED,
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
    data["occurred_at"] = normalize_utc(event.occurred_at)
    return data


def _row_to_model(row: DictRow) -> LifecycleTransition:
    """Convert a database row to a ``LifecycleTransition``.

    Returns:
        The deserialized transition.

    Raises:
        QueryError: If the row cannot be deserialized.
    """
    try:
        data = dict(row)
        # psycopg returns TIMESTAMPTZ in the session timezone, not necessarily
        # UTC; normalise on read so the model carries a UTC instant.
        data["occurred_at"] = normalize_utc(data["occurred_at"])
        return LifecycleTransition.model_validate(data)
    except ValidationError as exc:
        msg = f"Failed to deserialize transition {row.get('id')!r}"
        logger.warning(
            PERSISTENCE_LIFECYCLE_TRANSITION_DESERIALIZE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


__all__ = ["PostgresLifecycleTransitionRepository"]
