"""Postgres repositories for progressive trust state and change history.

Mirrors the SQLite implementation; the material differences are JSONB
columns for ``category_levels`` / ``milestone_progress`` (vs JSON text)
and TIMESTAMPTZ timestamps (vs ISO 8601 TEXT).
"""

from datetime import datetime

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import QueryError
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.trust_change_history import (
    PERSISTENCE_TRUST_CHANGE_HISTORY_APPEND_FAILED,
    PERSISTENCE_TRUST_CHANGE_HISTORY_QUERIED,
    PERSISTENCE_TRUST_CHANGE_HISTORY_QUERY_FAILED,
)
from synthorg.observability.events.persistence.trust_state import (
    PERSISTENCE_TRUST_STATE_QUERY_FAILED,
    PERSISTENCE_TRUST_STATE_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc, validate_pagination_args
from synthorg.persistence.trust_state_protocol import (
    TrustChangeHistoryFilterSpec,
)
from synthorg.security.trust.enums import TrustChangeReason
from synthorg.security.trust.models import TrustChangeRecord, TrustState

logger = get_logger(__name__)

_STATE_COLUMNS = (
    "agent_id, global_level, created_at, category_levels, trust_score, "
    "last_evaluated_at, last_promoted_at, last_decay_check_at, "
    "milestone_progress"
)
_STATE_UPSERT_SQL = """
INSERT INTO trust_states (
    agent_id, global_level, created_at, category_levels, trust_score,
    last_evaluated_at, last_promoted_at, last_decay_check_at,
    milestone_progress
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (agent_id) DO UPDATE SET
    global_level = EXCLUDED.global_level,
    created_at = EXCLUDED.created_at,
    category_levels = EXCLUDED.category_levels,
    trust_score = EXCLUDED.trust_score,
    last_evaluated_at = EXCLUDED.last_evaluated_at,
    last_promoted_at = EXCLUDED.last_promoted_at,
    last_decay_check_at = EXCLUDED.last_decay_check_at,
    milestone_progress = EXCLUDED.milestone_progress
"""

_HISTORY_COLUMNS = (
    "id, agent_id, old_level, new_level, category, reason, timestamp, "
    "approval_id, details"
)
_HISTORY_INSERT_SQL = """
INSERT INTO trust_change_history (
    id, agent_id, old_level, new_level, category, reason, timestamp,
    approval_id, details
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _coerce_score(raw: object) -> float | None:
    """Coerce a nullable DOUBLE PRECISION trust-score to ``float | None``.

    Returns:
        The score as a float, or ``None`` when the column is NULL.

    Raises:
        QueryError: If the column holds a non-numeric value.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    msg = f"trust_states.trust_score is not numeric: {raw!r}"
    raise QueryError(msg)


def _state_to_params(state: TrustState) -> tuple[object, ...]:
    """Marshal a ``TrustState`` into positional INSERT params.

    Returns:
        Positional params in ``_STATE_COLUMNS`` order.
    """
    category_levels = {k: v.value for k, v in state.category_levels.items()}
    return (
        str(state.agent_id),
        state.global_level.value,
        normalize_utc(state.created_at) if state.created_at else None,
        Jsonb(category_levels),
        state.trust_score,
        normalize_utc(state.last_evaluated_at) if state.last_evaluated_at else None,
        normalize_utc(state.last_promoted_at) if state.last_promoted_at else None,
        normalize_utc(state.last_decay_check_at) if state.last_decay_check_at else None,
        Jsonb(dict(state.milestone_progress)),
    )


def _row_to_state(row: DictRow) -> TrustState:
    """Deserialise a ``trust_states`` row into a ``TrustState``.

    Returns:
        The reconstructed ``TrustState``.

    Raises:
        QueryError: If a JSON column is not a JSON object.
    """
    categories_obj = row["category_levels"] or {}
    if not isinstance(categories_obj, dict):
        msg = f"trust_states.category_levels is not an object: {categories_obj!r}"
        raise QueryError(msg)
    category_levels = {
        str(k): ToolAccessLevel(str(v)) for k, v in categories_obj.items()
    }
    milestones_obj = row["milestone_progress"] or {}
    if not isinstance(milestones_obj, dict):
        msg = f"trust_states.milestone_progress is not an object: {milestones_obj!r}"
        raise QueryError(msg)
    return TrustState(
        agent_id=NotBlankStr(str(row["agent_id"])),
        global_level=ToolAccessLevel(str(row["global_level"])),
        created_at=normalize_utc(row["created_at"]) if row["created_at"] else None,
        category_levels=category_levels,
        trust_score=_coerce_score(row["trust_score"]),
        last_evaluated_at=normalize_utc(row["last_evaluated_at"])
        if row["last_evaluated_at"]
        else None,
        last_promoted_at=normalize_utc(row["last_promoted_at"])
        if row["last_promoted_at"]
        else None,
        last_decay_check_at=normalize_utc(row["last_decay_check_at"])
        if row["last_decay_check_at"]
        else None,
        milestone_progress=dict(milestones_obj),
    )


def _record_to_params(record: TrustChangeRecord) -> tuple[object, ...]:
    """Marshal a ``TrustChangeRecord`` into positional INSERT params.

    Returns:
        Positional params in ``_HISTORY_COLUMNS`` order.
    """
    return (
        str(record.id),
        str(record.agent_id),
        record.old_level.value,
        record.new_level.value,
        str(record.category) if record.category else None,
        record.reason.value,
        normalize_utc(record.timestamp),
        str(record.approval_id) if record.approval_id else None,
        record.details,
    )


def _row_to_record(row: DictRow) -> TrustChangeRecord:
    """Deserialise a ``trust_change_history`` row into a record.

    Returns:
        The reconstructed ``TrustChangeRecord``.
    """
    category = row["category"]
    approval_id = row["approval_id"]
    return TrustChangeRecord(
        id=NotBlankStr(str(row["id"])),
        agent_id=NotBlankStr(str(row["agent_id"])),
        old_level=ToolAccessLevel(str(row["old_level"])),
        new_level=ToolAccessLevel(str(row["new_level"])),
        category=NotBlankStr(str(category)) if category else None,
        reason=TrustChangeReason(str(row["reason"])),
        timestamp=normalize_utc(row["timestamp"]),
        approval_id=NotBlankStr(str(approval_id)) if approval_id else None,
        details=str(row["details"]) if row["details"] is not None else "",
    )


class PostgresTrustStateRepository:
    """Postgres upsert store for per-agent trust state.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool``.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: TrustState, /) -> None:
        """Upsert one trust state keyed on ``agent_id``.

        Raises:
            QueryError: If the write fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_STATE_UPSERT_SQL, _state_to_params(entity))
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to save trust state"
            logger.warning(
                PERSISTENCE_TRUST_STATE_SAVE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                agent_id=str(entity.agent_id),
            )
            raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr, /) -> TrustState | None:
        """Return the trust state for ``agent_id`` or ``None``.

        Returns:
            The trust state, or ``None`` when absent.

        Raises:
            QueryError: If the read fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_STATE_COLUMNS} FROM trust_states "  # noqa: S608
                    "WHERE agent_id = %s",
                    (str(entity_id),),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = "Failed to read trust state"
            logger.warning(
                PERSISTENCE_TRUST_STATE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return self._deserialize_state(row)

    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete the trust state for ``agent_id``.

        Returns:
            ``True`` iff a row existed.

        Raises:
            QueryError: If the delete fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM trust_states WHERE agent_id = %s",
                    (str(entity_id),),
                )
                removed = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to delete trust state"
            logger.warning(
                PERSISTENCE_TRUST_STATE_SAVE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                agent_id=str(entity_id),
            )
            raise QueryError(msg) from exc
        return removed > 0

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[TrustState, ...]:
        """List trust states ordered by ``agent_id`` ascending.

        Returns:
            Trust states, paginated.

        Raises:
            QueryError: If the read fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_TRUST_STATE_QUERY_FAILED
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_STATE_COLUMNS} FROM trust_states "  # noqa: S608
                    "ORDER BY agent_id ASC LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list trust states"
            logger.warning(
                PERSISTENCE_TRUST_STATE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._deserialize_state(r) for r in rows)

    def _deserialize_state(self, row: DictRow) -> TrustState:
        """Deserialise a row, failing closed on corruption.

        Returns:
            The reconstructed ``TrustState``.

        Raises:
            QueryError: If the row is corrupt.
        """
        try:
            return _row_to_state(row)
        except QueryError:
            raise
        except Exception as exc:
            msg = f"corrupt trust_states row for agent {row.get('agent_id')!r}"
            logger.warning(
                PERSISTENCE_TRUST_STATE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc


class PostgresTrustChangeHistoryRepository:
    """Postgres append-only audit trail of trust level transitions.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool``.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, event: TrustChangeRecord, /) -> None:
        """Append one immutable change record.

        Raises:
            QueryError: If the write fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_HISTORY_INSERT_SQL, _record_to_params(event))
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to append trust change record"
            logger.warning(
                PERSISTENCE_TRUST_CHANGE_HISTORY_APPEND_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                agent_id=str(event.agent_id),
            )
            raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: TrustChangeHistoryFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[TrustChangeRecord, ...]:
        """Return change records, newest-first, paginated.

        Returns:
            Matching change records, newest-first.

        Raises:
            QueryError: If the read fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_TRUST_CHANGE_HISTORY_QUERY_FAILED
        )
        sql = f"SELECT {_HISTORY_COLUMNS} FROM trust_change_history"  # noqa: S608
        params: list[object] = []
        if filter_spec.agent_id is not None:
            sql += " WHERE agent_id = %s"
            params.append(str(filter_spec.agent_id))
        sql += " ORDER BY timestamp DESC, id DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, tuple(params))
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query trust change history"
            logger.warning(
                PERSISTENCE_TRUST_CHANGE_HISTORY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            records = tuple(_row_to_record(r) for r in rows)
        except Exception as exc:
            msg = "corrupt trust_change_history row(s)"
            logger.warning(
                PERSISTENCE_TRUST_CHANGE_HISTORY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_TRUST_CHANGE_HISTORY_QUERIED,
            count=len(records),
        )
        return records

    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete change records older than ``threshold``.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the delete fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM trust_change_history WHERE timestamp < %s",
                    (normalize_utc(threshold),),
                )
                removed = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge trust change history"
            logger.warning(
                PERSISTENCE_TRUST_CHANGE_HISTORY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return removed
