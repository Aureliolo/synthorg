"""Postgres repository for project charters.

Sibling of :class:`SQLiteCharterRepository` backed by
``psycopg_pool.AsyncConnectionPool``. Satisfies ``CharterRepository``
structurally: id-keyed CRUD, atomic lifecycle transitions
(``drafted -> approved | cancelled``), and filtered queries.
"""

import json
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from synthorg.core.enums import CharterStatus
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.meta.charter.models import (
    BudgetEnvelope,
    ProjectCharter,
    ScopeBoundaries,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_CHARTER_FAILED,
    PERSISTENCE_CHARTER_FETCHED,
    PERSISTENCE_CHARTER_LISTED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.charter_protocol import CharterFilterSpec  # noqa: TC001

if TYPE_CHECKING:
    from typing import Any

    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000

_SELECT_COLS = (
    "id, conversation_id, created_by, version, status, title, brief, "
    "goals, constraints, success_criteria, in_scope, out_of_scope, "
    "envelope_amount, envelope_currency, envelope_deadline, "
    "envelope_time_horizon, project_id, proposed_project_name, "
    "proposed_project_description, created_at, updated_at, approved_at, "
    "approved_by, forecast_id, correlation_id, task_id"
)

_UPSERT_SQL = f"""
    INSERT INTO project_charters ({_SELECT_COLS})
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        conversation_id = EXCLUDED.conversation_id,
        created_by = EXCLUDED.created_by,
        version = EXCLUDED.version,
        status = EXCLUDED.status,
        title = EXCLUDED.title,
        brief = EXCLUDED.brief,
        goals = EXCLUDED.goals,
        constraints = EXCLUDED.constraints,
        success_criteria = EXCLUDED.success_criteria,
        in_scope = EXCLUDED.in_scope,
        out_of_scope = EXCLUDED.out_of_scope,
        envelope_amount = EXCLUDED.envelope_amount,
        envelope_currency = EXCLUDED.envelope_currency,
        envelope_deadline = EXCLUDED.envelope_deadline,
        envelope_time_horizon = EXCLUDED.envelope_time_horizon,
        project_id = EXCLUDED.project_id,
        proposed_project_name = EXCLUDED.proposed_project_name,
        proposed_project_description = EXCLUDED.proposed_project_description,
        updated_at = EXCLUDED.updated_at,
        approved_at = EXCLUDED.approved_at,
        approved_by = EXCLUDED.approved_by,
        forecast_id = EXCLUDED.forecast_id,
        correlation_id = EXCLUDED.correlation_id,
        task_id = EXCLUDED.task_id
"""  # noqa: S608 -- column list is a compile-time constant

_ALLOWED_TRANSITION_KEYS = frozenset(
    {
        "updated_at",
        "approved_at",
        "approved_by",
        "forecast_id",
        "correlation_id",
        "task_id",
    }
)


def _decode_str_tuple(raw: object) -> tuple[NotBlankStr, ...]:
    """Decode a JSON array column into a tuple of non-blank strings.

    Returns:
        The matching collection.
    """
    if raw is None:
        return ()
    decoded = json.loads(str(raw))
    return tuple(NotBlankStr(str(item)) for item in decoded)


def _encode_str_tuple(values: tuple[str, ...]) -> str:
    """Encode a string tuple as a deterministic JSON array.

    Returns:
        Result of type ``str``.
    """
    return json.dumps(list(values))


def _as_iso(value: object) -> str | None:
    """Normalise a timestamp update value to an ISO-8601 UTC string.

    Returns:
        The matching value, or ``None`` when absent.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return format_iso_utc(value)
    return str(value)


def _row_to_charter(row: dict[str, Any]) -> ProjectCharter:
    """Convert a Postgres dict row into a :class:`ProjectCharter`.

    Returns:
        Result of type ``ProjectCharter``.

    Raises:
        QueryError: If the database query fails.
    """
    try:
        deadline_raw = row["envelope_deadline"]
        approved_at_raw = row["approved_at"]
        forecast_raw = row["forecast_id"]
        envelope = BudgetEnvelope(
            amount=float(row["envelope_amount"]),
            currency=str(row["envelope_currency"]),
            deadline=(
                coerce_row_timestamp(deadline_raw) if deadline_raw is not None else None
            ),
            time_horizon=(
                str(row["envelope_time_horizon"])
                if row["envelope_time_horizon"] is not None
                else None
            ),
        )
        scope = ScopeBoundaries(
            in_scope=_decode_str_tuple(row["in_scope"]),
            out_of_scope=_decode_str_tuple(row["out_of_scope"]),
        )
        return ProjectCharter(
            id=NotBlankStr(str(row["id"])),
            conversation_id=NotBlankStr(str(row["conversation_id"])),
            created_by=NotBlankStr(str(row["created_by"])),
            version=int(row["version"]),
            status=CharterStatus(str(row["status"])),
            title=NotBlankStr(str(row["title"])),
            brief=NotBlankStr(str(row["brief"])),
            goals=_decode_str_tuple(row["goals"]),
            constraints=_decode_str_tuple(row["constraints"]),
            success_criteria=_decode_str_tuple(row["success_criteria"]),
            scope=scope,
            envelope=envelope,
            project_id=(
                NotBlankStr(str(row["project_id"]))
                if row["project_id"] is not None
                else None
            ),
            proposed_project_name=(
                NotBlankStr(str(row["proposed_project_name"]))
                if row["proposed_project_name"] is not None
                else None
            ),
            proposed_project_description=str(row["proposed_project_description"]),
            created_at=coerce_row_timestamp(row["created_at"]),
            updated_at=coerce_row_timestamp(row["updated_at"]),
            approved_at=(
                coerce_row_timestamp(approved_at_raw)
                if approved_at_raw is not None
                else None
            ),
            approved_by=(
                NotBlankStr(str(row["approved_by"]))
                if row["approved_by"] is not None
                else None
            ),
            forecast_id=(
                forecast_raw
                if isinstance(forecast_raw, UUID)
                else (UUID(str(forecast_raw)) if forecast_raw is not None else None)
            ),
            correlation_id=(
                NotBlankStr(str(row["correlation_id"]))
                if row["correlation_id"] is not None
                else None
            ),
            task_id=(
                NotBlankStr(str(row["task_id"])) if row["task_id"] is not None else None
            ),
        )
    except (ValueError, TypeError, KeyError) as exc:
        msg = (
            f"Failed to parse project charter row: "
            f"{type(exc).__name__} ({safe_error_description(exc)})"
        )
        logger.warning(
            PERSISTENCE_CHARTER_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


def _build_where(filter_spec: CharterFilterSpec) -> tuple[str, list[object]]:
    """Build the WHERE clause + bound params from a filter spec.

    Returns:
        ``(where_clause, params)``: SQL fragment + positional params.
    """
    clauses: list[str] = []
    params: list[object] = []
    if filter_spec.status is not None:
        clauses.append("status = %s")
        params.append(filter_spec.status.value)
    if filter_spec.project_id is not None:
        clauses.append("project_id = %s")
        params.append(filter_spec.project_id)
    if filter_spec.created_by is not None:
        clauses.append("created_by = %s")
        params.append(filter_spec.created_by)
    if filter_spec.conversation_id is not None:
        clauses.append("conversation_id = %s")
        params.append(filter_spec.conversation_id)
    where = " AND ".join(clauses) if clauses else "TRUE"
    return where, params


def _validate_update_keys(updates: dict[str, object]) -> None:
    """Reject unknown ``transition_if`` update keys.

    Raises:
        QueryError: If the database query fails.
    """
    unknown = sorted(set(updates) - _ALLOWED_TRANSITION_KEYS)
    if unknown:
        msg = f"transition_if rejects unknown update keys: {unknown!r}"
        logger.warning(PERSISTENCE_CHARTER_FAILED, operation="transition_if", error=msg)
        raise QueryError(msg)


def _charter_save_params(entity: ProjectCharter) -> tuple[object, ...]:
    """Flatten a charter into the positional upsert params.

    Returns:
        The matching collection.
    """
    return (
        entity.id,
        entity.conversation_id,
        entity.created_by,
        int(entity.version),
        entity.status.value,
        entity.title,
        entity.brief,
        _encode_str_tuple(entity.goals),
        _encode_str_tuple(entity.constraints),
        _encode_str_tuple(entity.success_criteria),
        _encode_str_tuple(entity.scope.in_scope),
        _encode_str_tuple(entity.scope.out_of_scope),
        float(entity.envelope.amount),
        entity.envelope.currency,
        (
            format_iso_utc(entity.envelope.deadline)
            if entity.envelope.deadline is not None
            else None
        ),
        entity.envelope.time_horizon,
        entity.project_id,
        entity.proposed_project_name,
        entity.proposed_project_description,
        format_iso_utc(entity.created_at),
        format_iso_utc(entity.updated_at),
        (
            format_iso_utc(entity.approved_at)
            if entity.approved_at is not None
            else None
        ),
        entity.approved_by,
        (str(entity.forecast_id) if entity.forecast_id is not None else None),
        entity.correlation_id,
        entity.task_id,
    )


class PostgresCharterRepository:
    """Postgres-backed project charter repository.

    Args:
        pool: Async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: ProjectCharter) -> None:
        """Upsert a charter row.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        params = _charter_save_params(entity)
        try:
            async with self._pool.connection() as conn:
                await conn.execute(_UPSERT_SQL, params)
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            msg = (
                f"Constraint violation saving charter {entity.id!r}: "
                f"{safe_error_description(exc)}"
            )
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="save",
                charter_id=entity.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(msg, constraint=str(exc)) from exc
        except psycopg.Error as exc:
            msg = (
                f"Failed to save charter {entity.id!r}: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="save",
                charter_id=entity.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> ProjectCharter | None:
        """Get a charter by id, or ``None`` if not found.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = f"SELECT {_SELECT_COLS} FROM project_charters WHERE id = %s"  # noqa: S608
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (entity_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch charter {entity_id!r}"
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="get",
                charter_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        charter = _row_to_charter(row)
        logger.debug(PERSISTENCE_CHARTER_FETCHED, charter_id=entity_id)
        return charter

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ProjectCharter, ...]:
        """List charters newest-first.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CHARTER_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        sql = (
            f"SELECT {_SELECT_COLS} FROM project_charters "  # noqa: S608
            "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (effective_limit, offset))
                rows = await cur.fetchall()
            items = tuple(_row_to_charter(r) for r in rows)
        except QueryError:
            raise
        except psycopg.Error as exc:
            msg = "Failed to list charters"
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="list_items",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_CHARTER_LISTED, count=len(items))
        return items

    async def query(
        self,
        filter_spec: CharterFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ProjectCharter, ...]:
        """Return charters matching the spec, newest-first (paginated).

        Returns:
            Tuple of (items, next_cursor) for paginated iteration.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CHARTER_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        where, params = _build_where(filter_spec)
        params.extend([effective_limit, offset])
        sql = f"""
            SELECT {_SELECT_COLS} FROM project_charters
            WHERE {where}
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s
        """  # noqa: S608 -- ``where`` is a closed set of column predicates
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
            items = tuple(_row_to_charter(r) for r in rows)
        except QueryError:
            raise
        except psycopg.Error as exc:
            msg = "Failed to query charters"
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="query",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_CHARTER_LISTED, count=len(items))
        return items

    async def count(self, filter_spec: CharterFilterSpec) -> int:
        """Count charters matching the filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        where, params = _build_where(filter_spec)
        sql = (
            "SELECT COUNT(*) FROM project_charters "  # noqa: S608
            f"WHERE {where}"
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
                assert row is not None  # noqa: S101 -- COUNT always returns a row
                return int(row[0])
        except psycopg.Error as exc:
            msg = "Failed to count charters"
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="count",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: CharterStatus,
        to_state: CharterStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for the charter lifecycle state.

        Returns:
            ``True`` when the operation succeeded, ``False`` otherwise.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        _validate_update_keys(updates)
        sql = (
            "UPDATE project_charters SET "
            "status = %s, "
            "updated_at = COALESCE(%s, updated_at), "
            "approved_at = COALESCE(%s, approved_at), "
            "approved_by = COALESCE(%s, approved_by), "
            "forecast_id = COALESCE(%s, forecast_id), "
            "correlation_id = COALESCE(%s, correlation_id), "
            "task_id = COALESCE(%s, task_id) "
            "WHERE id = %s AND status = %s"
        )
        forecast_update = updates.get("forecast_id")
        params = (
            to_state.value,
            _as_iso(updates.get("updated_at")),
            _as_iso(updates.get("approved_at")),
            updates.get("approved_by"),
            (str(forecast_update) if forecast_update is not None else None),
            updates.get("correlation_id"),
            updates.get("task_id"),
            entity_id,
            from_state.value,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            msg = (
                f"Constraint violation transitioning charter {entity_id!r}: "
                f"{safe_error_description(exc)}"
            )
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="transition_if",
                charter_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(msg, constraint=str(exc)) from exc
        except psycopg.Error as exc:
            msg = f"Failed to transition charter {entity_id!r}"
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="transition_if",
                charter_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return rowcount > 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a charter by id.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM project_charters WHERE id = %s"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, (entity_id,))
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete charter {entity_id!r}"
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="delete",
                charter_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return rowcount > 0


__all__ = ["PostgresCharterRepository"]
