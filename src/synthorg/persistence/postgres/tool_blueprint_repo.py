"""Postgres repository implementation for authored tool blueprints.

Sibling of :class:`SQLiteDynamicToolRepository` backed by
``psycopg_pool.AsyncConnectionPool``. Uses native ``JSONB`` for
``parameters_schema`` and ``validation``, ``BOOLEAN`` for
``requires_network``, and ``TIMESTAMPTZ`` for timestamps.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.meta.toolsmith.models import (
    ToolBlueprint,
    ToolBlueprintState,
    ToolSandboxBackend,
    ToolValidationResult,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_DYNAMIC_TOOL_DELETE_FAILED,
    PERSISTENCE_DYNAMIC_TOOL_DESERIALIZE_FAILED,
    PERSISTENCE_DYNAMIC_TOOL_FETCH_FAILED,
    PERSISTENCE_DYNAMIC_TOOL_FETCHED,
    PERSISTENCE_DYNAMIC_TOOL_LIST_FAILED,
    PERSISTENCE_DYNAMIC_TOOL_LISTED,
    PERSISTENCE_DYNAMIC_TOOL_QUERY_FAILED,
    PERSISTENCE_DYNAMIC_TOOL_SAVE_FAILED,
    PERSISTENCE_DYNAMIC_TOOL_TRANSITION_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    normalize_utc,
    validate_pagination_args,
)
from synthorg.persistence.tool_blueprint_protocol import (  # noqa: TC001
    ToolBlueprintFilterSpec,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: Final[int] = 1_000

# Status-correlated columns the CAS transition may stamp. ``validation``
# is included so a PENDING -> VALIDATED CAS can populate the gate-evidence
# atomically alongside ``validated_at``, satisfying the lifecycle CHECK
# that requires ``validation IS NOT NULL`` in the validated/active/retired
# branches.
_TRANSITION_UPDATE_KEYS: Final[frozenset[str]] = frozenset(
    {"validated_at", "activated_at", "retired_at", "validation"}
)

_SELECT_COLS = (
    "id, name, description, capability, parameters_schema, script_body, "
    "sandbox_backend, requires_network, action_type, state, created_at, "
    "validated_at, activated_at, retired_at, validation"
)

_UPSERT_SQL = f"""
    INSERT INTO dynamic_tools ({_SELECT_COLS})
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        description = EXCLUDED.description,
        capability = EXCLUDED.capability,
        parameters_schema = EXCLUDED.parameters_schema,
        script_body = EXCLUDED.script_body,
        sandbox_backend = EXCLUDED.sandbox_backend,
        requires_network = EXCLUDED.requires_network,
        action_type = EXCLUDED.action_type,
        state = EXCLUDED.state,
        validated_at = EXCLUDED.validated_at,
        activated_at = EXCLUDED.activated_at,
        retired_at = EXCLUDED.retired_at,
        validation = EXCLUDED.validation
"""  # noqa: S608 -- column list is compile-time constant


def _row_to_blueprint(row: dict[str, Any]) -> ToolBlueprint:
    """Convert a Postgres dict row into a :class:`ToolBlueprint`.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.

    Returns:
        Result of type ``ToolBlueprint``.
    """
    try:
        validation_raw = row["validation"]
        validation = (
            ToolValidationResult.model_validate(validation_raw)
            if validation_raw is not None
            else None
        )
        return ToolBlueprint(
            id=str(row["id"]),
            name=str(row["name"]),
            description=str(row["description"]),
            capability=str(row["capability"]),
            parameters_schema=row["parameters_schema"],
            script_body=str(row["script_body"]),
            sandbox_backend=ToolSandboxBackend(str(row["sandbox_backend"])),
            requires_network=bool(row["requires_network"]),
            action_type=str(row["action_type"]),
            state=ToolBlueprintState(str(row["state"])),
            created_at=coerce_row_timestamp(row["created_at"]),
            validated_at=(
                coerce_row_timestamp(row["validated_at"])
                if row["validated_at"] is not None
                else None
            ),
            activated_at=(
                coerce_row_timestamp(row["activated_at"])
                if row["activated_at"] is not None
                else None
            ),
            retired_at=(
                coerce_row_timestamp(row["retired_at"])
                if row["retired_at"] is not None
                else None
            ),
            validation=validation,
        )
    except (ValueError, TypeError, KeyError, ValidationError) as exc:
        try:
            row_id = str(row["id"]) if row else "<unknown>"
        except TypeError, KeyError:
            row_id = "<unknown>"
        msg = f"Failed to parse dynamic_tool row {row_id!r}"
        logger.warning(
            PERSISTENCE_DYNAMIC_TOOL_DESERIALIZE_FAILED,
            row_id=row_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


def _upsert_params(bp: ToolBlueprint) -> tuple[object, ...]:
    """Build the positional upsert parameter tuple for a blueprint.

    Returns:
        The matching collection.
    """
    return (
        bp.id,
        bp.name,
        bp.description,
        bp.capability,
        Jsonb(bp.parameters_schema),
        bp.script_body,
        bp.sandbox_backend.value,
        bp.requires_network,
        bp.action_type,
        bp.state.value,
        normalize_utc(bp.created_at),
        normalize_utc(bp.validated_at) if bp.validated_at is not None else None,
        normalize_utc(bp.activated_at) if bp.activated_at is not None else None,
        normalize_utc(bp.retired_at) if bp.retired_at is not None else None,
        Jsonb(bp.validation.model_dump(mode="json"))
        if bp.validation is not None
        else None,
    )


class PostgresDynamicToolRepository:
    """Postgres-backed authored tool blueprint repository.

    Args:
        pool: An open psycopg async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: ToolBlueprint) -> None:
        """Upsert a blueprint.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_UPSERT_SQL, _upsert_params(entity))
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            constraint = (
                getattr(getattr(exc, "diag", None), "constraint_name", None)
                or "<unknown>"
            )
            msg = f"Constraint violation saving dynamic_tool {entity.id!r}"
            logger.warning(
                PERSISTENCE_DYNAMIC_TOOL_SAVE_FAILED,
                blueprint_id=entity.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(msg, constraint=constraint) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save dynamic_tool {entity.id!r}"
            logger.warning(
                PERSISTENCE_DYNAMIC_TOOL_SAVE_FAILED,
                blueprint_id=entity.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> ToolBlueprint | None:
        """Get a blueprint by id, or ``None`` if not found.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = f"SELECT {_SELECT_COLS} FROM dynamic_tools WHERE id = %s"  # noqa: S608
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (entity_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch dynamic_tool {entity_id!r}"
            logger.warning(
                PERSISTENCE_DYNAMIC_TOOL_FETCH_FAILED,
                blueprint_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        blueprint = _row_to_blueprint(row)
        logger.debug(PERSISTENCE_DYNAMIC_TOOL_FETCHED, blueprint_id=entity_id)
        return blueprint

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ToolBlueprint, ...]:
        """List blueprints ordered by ``(created_at DESC, id DESC)``.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = min(
            validate_pagination_args(
                limit, offset, event=PERSISTENCE_DYNAMIC_TOOL_LIST_FAILED
            ),
            _MAX_PAGE_LIMIT,
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLS} FROM dynamic_tools "  # noqa: S608
                    "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                    (effective_limit, offset),
                )
                rows = await cur.fetchall()
                items = tuple(_row_to_blueprint(r) for r in rows)
        except psycopg.Error as exc:
            msg = "Failed to list dynamic_tools"
            logger.warning(
                PERSISTENCE_DYNAMIC_TOOL_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_DYNAMIC_TOOL_LISTED, count=len(items))
        return items

    def _build_where(
        self, filter_spec: ToolBlueprintFilterSpec
    ) -> tuple[str, list[object]]:
        """Build the WHERE clause and bound params from a filter spec.

        Returns:
            The matching collection.
        """
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.state is not None:
            clauses.append("state = %s")
            params.append(filter_spec.state.value)
        if filter_spec.capability is not None:
            clauses.append("capability = %s")
            params.append(filter_spec.capability)
        if filter_spec.sandbox_backend is not None:
            clauses.append("sandbox_backend = %s")
            params.append(filter_spec.sandbox_backend.value)
        where = " AND ".join(clauses) if clauses else "TRUE"
        return where, params

    async def query(
        self,
        filter_spec: ToolBlueprintFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ToolBlueprint, ...]:
        """List blueprints matching the filter spec (paginated).

        Returns:
            Tuple of (items, next_cursor) for paginated iteration.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = min(
            validate_pagination_args(
                limit, offset, event=PERSISTENCE_DYNAMIC_TOOL_QUERY_FAILED
            ),
            _MAX_PAGE_LIMIT,
        )
        where, params = self._build_where(filter_spec)
        params.extend([effective_limit, offset])
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLS} FROM dynamic_tools "  # noqa: S608
                    f"WHERE {where} ORDER BY created_at DESC, id DESC "
                    "LIMIT %s OFFSET %s",
                    params,
                )
                rows = await cur.fetchall()
                items = tuple(_row_to_blueprint(r) for r in rows)
        except psycopg.Error as exc:
            msg = "Failed to query dynamic_tools"
            logger.warning(
                PERSISTENCE_DYNAMIC_TOOL_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return items

    async def count(self, filter_spec: ToolBlueprintFilterSpec) -> int:
        """Count blueprints matching the filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        where, params = self._build_where(filter_spec)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    f"SELECT COUNT(*) FROM dynamic_tools WHERE {where}",  # noqa: S608
                    params,
                )
                row = await cur.fetchone()
                assert row is not None  # noqa: S101  -- COUNT always returns a row
                return int(row[0])
        except psycopg.Error as exc:
            msg = "Failed to count dynamic_tools"
            logger.warning(
                PERSISTENCE_DYNAMIC_TOOL_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: ToolBlueprintState,
        to_state: ToolBlueprintState,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for blueprint state transitions.

        ``**updates`` may carry ``validated_at`` / ``activated_at`` /
        ``retired_at`` / ``validation``; unknown keys raise ``QueryError``.
        Each kwarg is keyed off presence: omit it to leave the column
        unchanged, pass a value to update it. ``validation`` is serialised
        via ``model_dump_json()`` and wrapped in
        :class:`psycopg.types.json.Jsonb` when a
        :class:`ToolValidationResult` is passed; passing ``None``
        explicitly clears the JSONB column to ``NULL`` (callers who want
        to preserve existing evidence must omit the key).

        Returns:
            ``True`` when the operation succeeded, ``False`` otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        unknown = set(updates) - _TRANSITION_UPDATE_KEYS
        if unknown:
            msg = f"transition_if got unknown update keys: {sorted(unknown)!r}"
            logger.warning(
                PERSISTENCE_DYNAMIC_TOOL_TRANSITION_FAILED,
                blueprint_id=entity_id,
                error=msg,
            )
            raise QueryError(msg)
        set_cols = ["state = %s"]
        params: list[object] = [to_state.value]
        for key in ("validated_at", "activated_at", "retired_at"):
            if key in updates:
                set_cols.append(f"{key} = %s")
                params.append(_coerce_update_ts(updates[key]))
        if "validation" in updates:
            set_cols.append("validation = %s")
            params.append(_coerce_validation(updates["validation"]))
        params.extend([entity_id, from_state.value])
        sql = (
            f"UPDATE dynamic_tools SET {', '.join(set_cols)} "  # noqa: S608
            "WHERE id = %s AND state = %s"
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                updated = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to transition dynamic_tool {entity_id!r}"
            logger.warning(
                PERSISTENCE_DYNAMIC_TOOL_TRANSITION_FAILED,
                blueprint_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return updated

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a blueprint by id; ``True`` iff a row was removed.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM dynamic_tools WHERE id = %s"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, (entity_id,))
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete dynamic_tool {entity_id!r}"
            logger.warning(
                PERSISTENCE_DYNAMIC_TOOL_DELETE_FAILED,
                blueprint_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted


def _coerce_update_ts(value: object) -> datetime:
    """Normalise a transition timestamp kwarg to an aware-UTC datetime.

    Returns:
        Result of type ``datetime``.

    Raises:
        QueryError: If the database query fails.
    """
    if not isinstance(value, datetime):
        msg = f"transition timestamp must be a datetime, got {type(value).__name__}"
        raise QueryError(msg)
    return normalize_utc(value)


def _coerce_validation(value: object) -> Jsonb | None:
    """Render a transition ``validation`` kwarg to a JSONB-ready payload.

    ``None`` is passed through so callers can explicitly clear the column;
    everything else must be a :class:`ToolValidationResult`.

    Returns:
        The matching value, or ``None`` when absent.

    Raises:
        QueryError: If the database query fails.
    """
    if value is None:
        return None
    if not isinstance(value, ToolValidationResult):
        msg = (
            "transition validation must be a ToolValidationResult or None, "
            f"got {type(value).__name__}"
        )
        raise QueryError(msg)
    result: ToolValidationResult = value
    return Jsonb(result.model_dump(mode="json"))
