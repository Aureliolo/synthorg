"""SQLite repository implementation for authored tool blueprints."""

import json
import sqlite3
from datetime import datetime
from typing import Final

import aiosqlite
from aiosqlite import Row
from pydantic import ValidationError

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.meta.toolsmith.models import (
    ToolBlueprint,
    ToolBlueprintState,
    ToolSandboxBackend,
    ToolValidationResult,
)
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.persistence.dynamic_tool import (
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
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.sqlite._shared import WriteContext
from synthorg.persistence.tool_blueprint_protocol import (
    ToolBlueprintFilterSpec,
)

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

_UPSERT_SQL = """
    INSERT INTO dynamic_tools (
        id, name, description, capability, parameters_schema, script_body,
        sandbox_backend, requires_network, action_type, state, created_at,
        validated_at, activated_at, retired_at, validation
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        name = excluded.name,
        description = excluded.description,
        capability = excluded.capability,
        parameters_schema = excluded.parameters_schema,
        script_body = excluded.script_body,
        sandbox_backend = excluded.sandbox_backend,
        requires_network = excluded.requires_network,
        action_type = excluded.action_type,
        state = excluded.state,
        validated_at = excluded.validated_at,
        activated_at = excluded.activated_at,
        retired_at = excluded.retired_at,
        validation = excluded.validation
"""


def _row_to_blueprint(row: Row) -> ToolBlueprint:
    """Convert a database row to a :class:`ToolBlueprint`.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.

    Returns:
        Result of type ``ToolBlueprint``.
    """
    try:
        validation_raw = row["validation"]
        validation = (
            ToolValidationResult.model_validate_json(str(validation_raw))
            if validation_raw is not None
            else None
        )
        return ToolBlueprint(
            id=str(row["id"]),
            name=str(row["name"]),
            description=str(row["description"]),
            capability=str(row["capability"]),
            parameters_schema=json.loads(str(row["parameters_schema"])),
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
    except (
        json.JSONDecodeError,
        ValueError,
        TypeError,
        KeyError,
        ValidationError,
    ) as exc:
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
        json.dumps(bp.parameters_schema),
        bp.script_body,
        bp.sandbox_backend.value,
        1 if bp.requires_network else 0,
        bp.action_type,
        bp.state.value,
        format_iso_utc(bp.created_at),
        format_iso_utc(bp.validated_at) if bp.validated_at else None,
        format_iso_utc(bp.activated_at) if bp.activated_at else None,
        format_iso_utc(bp.retired_at) if bp.retired_at else None,
        bp.validation.model_dump_json() if bp.validation is not None else None,
    )


class SQLiteDynamicToolRepository:
    """SQLite-backed authored tool blueprint repository.

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
        self._db.row_factory = aiosqlite.Row
        self._write_context = write_context

    async def save(self, entity: ToolBlueprint) -> None:
        """Upsert a blueprint.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        async with self._write_context():
            try:
                await self._db.execute(_UPSERT_SQL, _upsert_params(entity))
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await self._rollback(
                    event=PERSISTENCE_DYNAMIC_TOOL_SAVE_FAILED,
                    operation="save",
                )
                msg = f"Constraint violation saving dynamic_tool {entity.id!r}"
                logger.warning(
                    PERSISTENCE_DYNAMIC_TOOL_SAVE_FAILED,
                    blueprint_id=entity.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise ConstraintViolationError(msg, constraint=str(exc)) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback(
                    event=PERSISTENCE_DYNAMIC_TOOL_SAVE_FAILED,
                    operation="save",
                )
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
        sql = f"SELECT {_SELECT_COLS} FROM dynamic_tools WHERE id = ?"  # noqa: S608
        try:
            async with self._db.execute(sql, (entity_id,)) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        sql = (
            f"SELECT {_SELECT_COLS} FROM dynamic_tools "  # noqa: S608
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        )
        try:
            async with self._db.execute(sql, (effective_limit, offset)) as cursor:
                rows = await cursor.fetchall()
            items = tuple(_row_to_blueprint(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            ``(where_clause, params)`` where ``where_clause`` is the SQL fragment
            (without the leading ``WHERE``) and ``params`` is the matching positional
            parameter list.
        """
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.state is not None:
            clauses.append("state = ?")
            params.append(filter_spec.state.value)
        if filter_spec.capability is not None:
            clauses.append("capability = ?")
            params.append(filter_spec.capability)
        if filter_spec.sandbox_backend is not None:
            clauses.append("sandbox_backend = ?")
            params.append(filter_spec.sandbox_backend.value)
        where = " AND ".join(clauses) if clauses else "1=1"
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
            The matching entities.

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
        sql = (
            f"SELECT {_SELECT_COLS} FROM dynamic_tools WHERE {where} "  # noqa: S608
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        )
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
            items = tuple(_row_to_blueprint(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        sql = f"SELECT COUNT(*) FROM dynamic_tools WHERE {where}"  # noqa: S608
        try:
            async with self._db.execute(sql, params) as cursor:
                row = await cursor.fetchone()
            assert row is not None  # noqa: S101  -- COUNT always returns a row
            return int(row[0])
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        via ``model_dump_json()`` when a :class:`ToolValidationResult` is
        passed; passing ``None`` explicitly clears the column to ``NULL``
        (callers who want to preserve existing evidence must omit the key).

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
        set_cols = ["state = ?"]
        params: list[object] = [to_state.value]
        for key in ("validated_at", "activated_at", "retired_at"):
            if key in updates:
                set_cols.append(f"{key} = ?")
                params.append(_coerce_update_ts(updates[key]))
        if "validation" in updates:
            set_cols.append("validation = ?")
            params.append(_coerce_validation(updates["validation"]))
        params.extend([entity_id, from_state.value])
        sql = (
            f"UPDATE dynamic_tools SET {', '.join(set_cols)} "  # noqa: S608
            "WHERE id = ? AND state = ?"
        )
        async with self._write_context():
            try:
                async with self._db.execute(sql, params) as cursor:
                    await self._db.commit()
                    _db_rowcount = cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback(
                    event=PERSISTENCE_DYNAMIC_TOOL_TRANSITION_FAILED,
                    operation="transition_if",
                )
                msg = f"Failed to transition dynamic_tool {entity_id!r}"
                logger.warning(
                    PERSISTENCE_DYNAMIC_TOOL_TRANSITION_FAILED,
                    blueprint_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return _db_rowcount > 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a blueprint by id; ``True`` iff a row was removed.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM dynamic_tools WHERE id = ?"
        async with self._write_context():
            try:
                async with self._db.execute(sql, (entity_id,)) as cursor:
                    await self._db.commit()
                    _db_rowcount = cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback(
                    event=PERSISTENCE_DYNAMIC_TOOL_DELETE_FAILED,
                    operation="delete",
                )
                msg = f"Failed to delete dynamic_tool {entity_id!r}"
                logger.warning(
                    PERSISTENCE_DYNAMIC_TOOL_DELETE_FAILED,
                    blueprint_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return _db_rowcount > 0

    async def _rollback(self, *, event: str, operation: str) -> None:
        """Roll back the current transaction, swallowing rollback errors.

        Args:
            event: Event constant identifying the originating operation
                so alerting can attribute rollback failures to the
                correct ``save`` / ``transition`` / ``delete`` path
                rather than the SAVE-only event the shared helper used
                to hard-code.
            operation: Short operation tag (``save`` / ``transition_if`` /
                ``delete``) added as a structured kwarg so the same
                rollback failure can be filtered on operation type
                without parsing the event constant.
        """
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            log_exception_redacted(
                logger, event, exc, phase="rollback", operation=operation
            )


def _coerce_update_ts(value: object) -> str:
    """Render a transition timestamp kwarg to an ISO-8601 UTC string.

    Returns:
        Result of type ``str``.

    Raises:
        QueryError: If ``value`` is not a ``datetime``.
    """
    if not isinstance(value, datetime):
        msg = f"transition timestamp must be a datetime, got {type(value).__name__}"
        raise QueryError(msg)
    return format_iso_utc(value)


def _coerce_validation(value: object) -> str | None:
    """Render a transition ``validation`` kwarg to a JSON object string.

    ``None`` is passed through so callers can explicitly clear the column;
    everything else must be a :class:`ToolValidationResult`.

    Returns:
        The matching value, or ``None`` when absent.

    Raises:
        QueryError: If ``value`` is neither ``None`` nor a ``ToolValidationResult``.
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
    return result.model_dump_json()
