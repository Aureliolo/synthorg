# module-kind: code
"""Read path for the SQLite decision repository.

All reads run under the shared ``write_context`` so they never observe
rows from an in-flight ``INSERT -> SELECT -> commit`` sequence that has
not yet committed.
"""

import json
import sqlite3
from uuid import UUID

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import MalformedRowError, QueryError
from synthorg.core.types import NotBlankStr
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
from synthorg.persistence.sqlite.decision._base import _DecisionRepoBase
from synthorg.persistence.sqlite.decision._sql import (
    _COLS,
    _MAX_PAGE_LIMIT,
    _ROLE_TO_COLUMN,
)

logger = get_logger(__name__)


class _QueryMixin(_DecisionRepoBase):
    """Read path for ``SQLiteDecisionRepository``."""

    async def query(
        self,
        filter_spec: DecisionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DecisionRecord, ...]:
        """Query decision records with optional filters and pagination.

        When only task_id is specified, results are oldest-first
        (ascending recorded_at). When agent_id and role are specified
        without task_id, results are newest-first. Mixed filters default
        to task-oriented (oldest-first) ordering.

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

        # Determine ordering and WHERE clause based on filter spec.
        task_id_filter = filter_spec.task_id
        agent_id_filter = filter_spec.agent_id
        role_filter = filter_spec.role

        where_clauses: list[str] = []
        params: list[object] = []

        if task_id_filter is not None:
            where_clauses.append("task_id = ?")
            params.append(task_id_filter)

        if agent_id_filter is not None and role_filter is not None:
            if role_filter == "executor":
                where_clauses.append("executing_agent_id = ?")
            else:
                where_clauses.append("reviewer_agent_id = ?")
            params.append(agent_id_filter)
        elif agent_id_filter is not None:
            where_clauses.append("(executing_agent_id = ? OR reviewer_agent_id = ?)")
            params.extend((agent_id_filter, agent_id_filter))

        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"

        if task_id_filter is not None:
            order_by = "recorded_at ASC, id ASC"
        else:
            order_by = "recorded_at DESC, id DESC"

        try:
            async with self._write_context():
                cursor = await self._db.execute(
                    f"""\
                    SELECT {_COLS} FROM decision_records
                    WHERE {where_clause}
                    ORDER BY {order_by}
                    LIMIT ? OFFSET ?""",  # noqa: S608
                    (*params, effective_limit, offset),
                )
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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

        Serialized against concurrent writers via ``write_context`` so
        reads never observe rows from an in-flight ``INSERT -> SELECT
        -> commit`` sequence that has not yet committed.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._write_context():
                cursor = await self._db.execute(
                    f"SELECT {_COLS} FROM decision_records WHERE id = ?",  # noqa: S608
                    (record_id,),
                )
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        return self._row_to_record(dict(row))

    async def list_by_task(
        self,
        task_id: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DecisionRecord, ...]:
        """List decision records for a task, oldest first.

        Serialized against concurrent writers via ``write_context`` so
        reads never observe phantom rows from a mid-transaction
        ``append_with_next_version``.

        Args:
            task_id: Identifier of the task whose decisions are being
                listed.
            limit: Maximum number of records to return on this page;
                must be ``>= 1``. The repo additionally clamps the
                returned slice to ``_MAX_PAGE_LIMIT`` to prevent a
                runaway caller from materialising the full table.
            offset: Number of records to skip before the page; must
                be ``>= 0``.

        Returns:
            ``tuple[DecisionRecord, ...]`` ordered ascending by
            ``(recorded_at, id)`` so a backfilled decision still
            sorts to its true chronological position; the ``id``
            tiebreaker matches the Postgres backend.

        Raises:
            QueryError: If ``limit`` / ``offset`` fail the type or
                bounds check, or if the underlying SQLite query
                raises. The structured ``WARNING`` is emitted before
                the raise.
        """
        validate_pagination_args(
            limit,
            offset,
            event=PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
            task_id=task_id,
        )
        effective_limit = min(limit, _MAX_PAGE_LIMIT)
        try:
            async with self._write_context():
                # ``recorded_at ASC, id ASC`` matches the protocol's
                # "oldest first" contract; ``version ASC`` would
                # mis-place a backfilled decision (low ``recorded_at``
                # but a freshly-allocated high ``version``) at the end
                # of the list. Mirrors the Postgres backend.
                cursor = await self._db.execute(
                    f"SELECT {_COLS} FROM decision_records "  # noqa: S608
                    "WHERE task_id = ? "
                    "ORDER BY recorded_at ASC, id ASC LIMIT ? OFFSET ?",
                    (task_id, effective_limit, offset),
                )
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to list decision records for task {task_id!r}"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        results = tuple(self._row_to_record(dict(row)) for row in rows)
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

        ``role`` is validated via ``Literal`` at the type level, but we
        re-check at runtime to guard against bad callers that bypass
        type checking.  A rejected role is logged before raising.
        Serialized against concurrent writers via ``write_context``.

        Args:
            agent_id: Identifier of the agent whose decisions are
                being listed.
            role: Either ``"executor"`` or ``"reviewer"``; selects
                which side of the decision the agent participated on.
                Anything outside that set raises ``QueryError``.
            limit: Maximum number of records to return on this page;
                must be ``>= 1``. Clamped to ``_MAX_PAGE_LIMIT`` to
                prevent unbounded queries.
            offset: Number of records to skip before the page; must
                be ``>= 0``.

        Returns:
            ``tuple[DecisionRecord, ...]`` ordered by
            ``(recorded_at DESC, id DESC)`` so newest decisions come
            first. The ``id`` tiebreaker matches the Postgres
            backend and keeps page boundaries stable under
            concurrent inserts.

        Raises:
            QueryError: If ``role`` is outside the closed set, if
                ``limit`` / ``offset`` fail the type or bounds check,
                or if the underlying SQLite query raises.
        """
        # Runtime defense in depth: the Literal prevents type-safe
        # callers from passing bad values, but untyped callers can
        # still pass anything.  Check the input TYPE first so a
        # list/dict/None argument raises ``ValueError`` with the
        # same message shape as an unknown-string role, instead of
        # a surprising ``TypeError`` (unhashable) inside the dict
        # lookup.  Using a dict lookup instead of if/elif keeps the
        # column name derivation closed over a bounded set of
        # hard-coded identifiers (see the closed-set comment on
        # the SQL query below).  mypy narrows ``role`` to
        # ``Literal[...]`` and treats this branch as unreachable,
        # which is exactly the static case -- but runtime callers
        # can still defeat the Literal.
        # Cast to ``object`` so mypy doesn't narrow to ``Literal``
        # and mark the untyped-caller defense as unreachable.
        role_obj: object = role
        if not isinstance(role_obj, str):
            msg = (
                f"role must be 'executor' or 'reviewer', got {type(role_obj).__name__}"
            )
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                agent_id=agent_id,
                role_type=type(role_obj).__name__,
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
            # column is a closed-set value from _ROLE_TO_COLUMN, never
            # user-supplied; agent_id flows through the positional
            # placeholder. ``id`` is added to the ORDER BY tiebreaker
            # so cursor pagination stays deterministic when two records
            # share a ``recorded_at`` timestamp.
            query = (
                f"SELECT {_COLS} FROM decision_records "  # noqa: S608
                f"WHERE {column} = ? ORDER BY recorded_at DESC, id DESC "
                f"LIMIT ? OFFSET ?"
            )
            async with self._write_context():
                cursor = await self._db.execute(
                    query,
                    (agent_id, effective_limit, offset),
                )
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        results = tuple(self._row_to_record(dict(row)) for row in rows)
        logger.debug(
            PERSISTENCE_DECISION_RECORD_QUERIED,
            agent_id=agent_id,
            role=role,
            count=len(results),
        )
        return results

    def _row_to_record(self, row: dict[str, object]) -> DecisionRecord:
        """Convert a database row to a ``DecisionRecord`` model.

        Every required column is read via explicit ``row["col"]``
        indexing so a missing column (schema drift) surfaces as
        ``KeyError`` with the specific column name logged via
        ``PERSISTENCE_DECISION_RECORD_DESERIALIZE_FAILED`` before the
        exception re-raises.  Building ``parsed`` via ``dict(row)``
        would silently copy whatever's present and defer the failure
        to ``DecisionRecord.model_validate`` with a less informative
        ``ValidationError``, so we assemble it field-by-field
        instead.

        The JSON-encoded ``criteria_snapshot`` column is shape-checked
        after deserialization: a row that somehow stores a non-array
        (e.g. a bare string or object, from a migration bug or a
        third-party backend) is rejected with ``QueryError`` rather
        than being silently coerced via ``tuple(...)`` which would
        iterate over the object's keys / string characters and
        produce garbage data.

        Returns:
            The reconstructed ``DecisionRecord``.

        Raises:
            QueryError: If row deserialization or validation fails.
            KeyError: If a required dictionary key is missing.
        """  # noqa: DOC501 -- TypeError is caught locally and surfaces as QueryError
        try:
            try:
                # Explicit reads for every required column.  Any
                # missing key raises KeyError and hits the log-and-
                # re-raise handler below.
                parsed: dict[str, object] = {
                    "id": UUID(str(row["id"])),
                    "task_id": row["task_id"],
                    "approval_id": row["approval_id"],
                    "executing_agent_id": row["executing_agent_id"],
                    "reviewer_agent_id": row["reviewer_agent_id"],
                    "decision": row["decision"],
                    "reason": row["reason"],
                    "recorded_at": row["recorded_at"],
                    "version": row["version"],
                }
                raw_criteria = row["criteria_snapshot"]
                raw_metadata = row["metadata"]
            except KeyError as exc:
                missing = exc.args[0] if exc.args else None
                logger.warning(
                    PERSISTENCE_DECISION_RECORD_DESERIALIZE_FAILED,
                    record_id=row.get("id"),
                    missing_column=missing,
                    error_type="KeyError",
                    error=safe_error_description(exc),
                )
                raise
            if isinstance(raw_criteria, str):
                decoded_criteria = json.loads(raw_criteria)
                if not isinstance(decoded_criteria, list):
                    msg = (
                        f"criteria_snapshot for decision record "
                        f"{row.get('id')!r} is not a JSON array "
                        f"(got {type(decoded_criteria).__name__})"
                    )
                    raise TypeError(msg)  # noqa: TRY301
                parsed["criteria_snapshot"] = tuple(decoded_criteria)
            else:
                parsed["criteria_snapshot"] = raw_criteria
            if isinstance(raw_metadata, str):
                parsed["metadata"] = json.loads(raw_metadata)
            else:
                parsed["metadata"] = raw_metadata
            return DecisionRecord.model_validate(parsed)
        except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as exc:
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
            raise MalformedRowError(msg) from exc
