# module-kind: code
"""Read path for the Postgres decision repository.

JSONB columns arrive as native Python ``list`` / ``dict`` and
TIMESTAMPTZ as timezone-aware ``datetime``, so there is no
``json.loads`` step on the way out (the string branch in
``_coerce_criteria`` is defensive for SQLite-migrated data).
"""

import json
from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.engine.decisions import DecisionRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.decision_record import (
    PERSISTENCE_DECISION_RECORD_DESERIALIZE_FAILED,
    PERSISTENCE_DECISION_RECORD_QUERIED,
    PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence.decision_protocol import DecisionFilterSpec, DecisionRole
from synthorg.persistence.postgres.decision._base import _DecisionRepoBase
from synthorg.persistence.postgres.decision._sql import (
    _COLS,
    _MAX_PAGE_LIMIT,
    _ROLE_TO_COLUMN,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)


class _QueryMixin(_DecisionRepoBase):
    """Read path for ``PostgresDecisionRepository``."""

    async def query(
        self,
        filter_spec: DecisionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DecisionRecord, ...]:
        """Query decision records with optional filters and pagination.

        When only ``task_id`` is set, results are oldest-first
        (ascending ``recorded_at``).  When ``agent_id`` and ``role``
        are set without ``task_id``, results are newest-first.  Mixed
        filters default to task-oriented (oldest-first) ordering.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        validate_pagination_args(
            limit,
            offset,
            event=PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
        )
        effective_limit = min(limit, _MAX_PAGE_LIMIT)

        task_id_filter = filter_spec.task_id
        agent_id_filter = filter_spec.agent_id
        role_filter = filter_spec.role

        where_clauses: list[str] = []
        params_list: list[object] = []

        if task_id_filter is not None:
            where_clauses.append("task_id = %s")
            params_list.append(task_id_filter)

        if agent_id_filter is not None and role_filter is not None:
            if role_filter == "executor":
                where_clauses.append("executing_agent_id = %s")
            else:  # "reviewer"
                where_clauses.append("reviewer_agent_id = %s")
            params_list.append(agent_id_filter)
        elif agent_id_filter is not None:
            where_clauses.append("(executing_agent_id = %s OR reviewer_agent_id = %s)")
            params_list.extend([agent_id_filter, agent_id_filter])

        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"

        if task_id_filter is not None:
            order_by = "recorded_at ASC, id ASC"
        else:
            order_by = "recorded_at DESC, id DESC"

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                query_sql = f"""\
                SELECT {_COLS} FROM decision_records
                WHERE {where_clause}
                ORDER BY {order_by}
                LIMIT %s OFFSET %s"""  # noqa: S608
                params_list.extend([effective_limit, offset])
                await cur.execute(query_sql, params_list)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query decision records"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        results = tuple(self._row_to_record(dict(row)) for row in rows)
        logger.debug(
            PERSISTENCE_DECISION_RECORD_QUERIED,
            count=len(results),
        )
        return results

    async def get(self, record_id: NotBlankStr) -> DecisionRecord | None:
        """Retrieve a decision record by ID.

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
                    f"SELECT {_COLS} FROM decision_records WHERE id = %s",  # noqa: S608
                    (record_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch decision record {record_id!r}"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                record_id=record_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return self._row_to_record(row)

    async def list_by_task(
        self,
        task_id: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DecisionRecord, ...]:
        """List decision records for a task, oldest first.

        Args:
            task_id: Identifier of the task whose decisions are being
                listed.
            limit: Maximum number of records to return on this page;
                must be ``>= 1``. Clamped to ``_MAX_PAGE_LIMIT``.
            offset: Number of records to skip before the page; must
                be ``>= 0``.

        Returns:
            ``tuple[DecisionRecord, ...]`` ordered ascending by
            ``(recorded_at, id)``.

        Raises:
            QueryError: If ``limit`` / ``offset`` fail the type or
                bounds check, or if the underlying ``psycopg`` query
                raises.
        """
        validate_pagination_args(
            limit,
            offset,
            event=PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
            task_id=task_id,
        )
        effective_limit = min(limit, _MAX_PAGE_LIMIT)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_COLS} FROM decision_records "  # noqa: S608
                    "WHERE task_id = %s "
                    "ORDER BY recorded_at ASC, id ASC LIMIT %s OFFSET %s",
                    (task_id, effective_limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = f"Failed to list decision records for task {task_id!r}"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        results = tuple(self._row_to_record(row) for row in rows)
        logger.debug(
            PERSISTENCE_DECISION_RECORD_QUERIED,
            task_id=task_id,
            count=len(results),
        )
        return results

    async def list_by_agent(
        self,
        agent_id: NotBlankStr,
        *,
        role: DecisionRole,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DecisionRecord, ...]:
        """List decision records where the agent acted in the given role.

        Args:
            agent_id: Identifier of the agent whose decisions are
                being listed.
            role: Either ``"executor"`` or ``"reviewer"``.
            limit: Maximum number of records to return on this page;
                must be ``>= 1``. Clamped to ``_MAX_PAGE_LIMIT``.
            offset: Number of records to skip before the page; must
                be ``>= 0``.

        Returns:
            ``tuple[DecisionRecord, ...]`` ordered ``(recorded_at DESC,
            id DESC)``.

        Raises:
            QueryError: If ``role`` is outside the closed set, if
                ``limit`` / ``offset`` fail the type or bounds check,
                or if the underlying ``psycopg`` query raises.
        """
        # Runtime defense: validate role is in the closed set
        role_obj: object = role
        if not isinstance(role_obj, str):
            got = type(role_obj).__name__
            msg = f"role must be 'executor' or 'reviewer', got {got}"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                agent_id=agent_id,
                role_type=got,
                error=msg,
            )
            raise QueryError(msg)
        role_str: str = role_obj
        try:
            column = _ROLE_TO_COLUMN[role_str]
        except KeyError as exc:
            msg = f"role must be 'executor' or 'reviewer', got {role_str!r}"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                agent_id=agent_id,
                role=role_str,
                error=msg,
            )
            raise QueryError(msg) from exc
        validate_pagination_args(
            limit,
            offset,
            event=PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
            agent_id=agent_id,
            role=role_str,
        )
        effective_limit = min(limit, _MAX_PAGE_LIMIT)
        try:
            # column is a closed-set value from _ROLE_TO_COLUMN.
            # ``id DESC`` tiebreaker keeps cursor pagination stable
            # when records share a recorded_at timestamp.
            query = (
                f"SELECT {_COLS} FROM decision_records "  # noqa: S608
                f"WHERE {column} = %s ORDER BY recorded_at DESC, id DESC "
                f"LIMIT %s OFFSET %s"
            )
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(query, (agent_id, effective_limit, offset))
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = (
                f"Failed to list decision records for agent {agent_id!r} (role={role})"
            )
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                agent_id=agent_id,
                role=role,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        results = tuple(self._row_to_record(row) for row in rows)
        logger.debug(
            PERSISTENCE_DECISION_RECORD_QUERIED,
            agent_id=agent_id,
            role=role,
            count=len(results),
        )
        return results

    @staticmethod
    def _coerce_criteria(raw_criteria: object, record_id: object) -> tuple[object, ...]:
        """Normalize a ``criteria_snapshot`` JSONB value to a tuple.

        Postgres JSONB comes back as ``list``/``dict``; the "string"
        branch is defensive against callers that migrate data from
        the SQLite backend (which stored criteria as a JSON string).

        Returns:
            The matching collection.

        Raises:
            TypeError: If an argument has the wrong type.
        """
        if isinstance(raw_criteria, str):
            decoded = json.loads(raw_criteria)
            if not isinstance(decoded, list):
                msg = (
                    f"criteria_snapshot for decision record "
                    f"{record_id!r} is not a JSON array "
                    f"(got {type(decoded).__name__})"
                )
                raise TypeError(msg)
            return tuple(decoded)
        if not isinstance(raw_criteria, list):
            msg = (
                f"criteria_snapshot for decision record {record_id!r} "
                f"is not a list (got {type(raw_criteria).__name__})"
            )
            raise TypeError(msg)
        return tuple(raw_criteria)

    def _row_to_record(self, row: dict[str, object]) -> DecisionRecord:
        """Convert a database row to a ``DecisionRecord`` model.

        JSONB columns in Postgres come back as dicts/lists, not strings.
        The ``criteria_snapshot`` is shape-checked to ensure it's a list.
        All failure modes (missing columns, malformed JSON, shape
        mismatches, Pydantic validation errors) are normalized into
        ``QueryError`` with a consistent event payload so callers get
        consistent telemetry whatever the failure shape.

        Args:
            row: A database row as a dict (from ``dict_row`` factory).

        Returns:
            The matching entity.

        Raises:
            QueryError: If row deserialization or validation fails
                (missing columns, malformed JSON, shape mismatch, or
                Pydantic validation error are all normalized to this).
        """
        try:
            # Explicit reads for every required column; a missing key
            # raises ``KeyError`` (naturally, no explicit ``raise``) and
            # is normalized to ``QueryError`` below alongside the other
            # failure modes.
            record_data: dict[str, object] = {
                "id": row["id"],
                "task_id": row["task_id"],
                "approval_id": row["approval_id"],
                "executing_agent_id": row["executing_agent_id"],
                "reviewer_agent_id": row["reviewer_agent_id"],
                "decision": row["decision"],
                "reason": row["reason"],
                "recorded_at": row["recorded_at"],
                "version": row["version"],
            }
            record_data["criteria_snapshot"] = self._coerce_criteria(
                row["criteria_snapshot"], row.get("id")
            )
            raw_metadata = row["metadata"]
            record_data["metadata"] = (
                raw_metadata if isinstance(raw_metadata, dict) else {}
            )
            return DecisionRecord.model_validate(record_data)
        except (KeyError, ValidationError, TypeError, json.JSONDecodeError) as exc:
            msg = (
                f"Failed to deserialize decision record {row.get('id')!r}: "
                f"{type(exc).__name__}"
            )
            logger.warning(
                PERSISTENCE_DECISION_RECORD_DESERIALIZE_FAILED,
                record_id=row.get("id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
