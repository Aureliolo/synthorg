"""SQLite repositories for progressive trust state and change history.

``SQLiteTrustStateRepository`` is an idempotent upsert store keyed by
``agent_id``; ``SQLiteTrustChangeHistoryRepository`` is an append-only
audit trail read newest-first. Both back
:class:`synthorg.security.trust.service.TrustService`.
"""

import json
import sqlite3
from datetime import datetime

import aiosqlite

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
    PERSISTENCE_TRUST_STATE_DELETE_FAILED,
    PERSISTENCE_TRUST_STATE_QUERY_FAILED,
    PERSISTENCE_TRUST_STATE_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence._shared.datetime_marshaller import (
    format_iso_utc,
    parse_iso_utc,
)
from synthorg.persistence.sqlite._shared import WriteContext
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
INSERT OR REPLACE INTO trust_states (
    agent_id, global_level, created_at, category_levels, trust_score,
    last_evaluated_at, last_promoted_at, last_decay_check_at,
    milestone_progress
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _coerce_score(raw: object) -> float | None:
    """Coerce a nullable REAL trust-score column to ``float | None``.

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
        format_iso_utc(state.created_at) if state.created_at else None,
        json.dumps(category_levels, sort_keys=True),
        state.trust_score,
        format_iso_utc(state.last_evaluated_at) if state.last_evaluated_at else None,
        format_iso_utc(state.last_promoted_at) if state.last_promoted_at else None,
        format_iso_utc(state.last_decay_check_at)
        if state.last_decay_check_at
        else None,
        json.dumps(state.milestone_progress, sort_keys=True),
    )


def _row_to_state(row: dict[str, object]) -> TrustState:
    """Deserialise a ``trust_states`` row into a ``TrustState``.

    Returns:
        The reconstructed ``TrustState``.

    Raises:
        QueryError: If a JSON column is not a JSON object.
    """
    raw_categories = row["category_levels"]
    categories_obj = json.loads(str(raw_categories)) if raw_categories else {}
    if not isinstance(categories_obj, dict):
        msg = f"trust_states.category_levels is not an object: {categories_obj!r}"
        raise QueryError(msg)
    category_levels = {
        str(k): ToolAccessLevel(str(v)) for k, v in categories_obj.items()
    }
    raw_milestones = row["milestone_progress"]
    milestones_obj = json.loads(str(raw_milestones)) if raw_milestones else {}
    if not isinstance(milestones_obj, dict):
        msg = f"trust_states.milestone_progress is not an object: {milestones_obj!r}"
        raise QueryError(msg)
    return TrustState(
        agent_id=NotBlankStr(str(row["agent_id"])),
        global_level=ToolAccessLevel(str(row["global_level"])),
        created_at=parse_iso_utc(str(row["created_at"])) if row["created_at"] else None,
        category_levels=category_levels,
        trust_score=_coerce_score(row["trust_score"]),
        last_evaluated_at=parse_iso_utc(str(row["last_evaluated_at"]))
        if row["last_evaluated_at"]
        else None,
        last_promoted_at=parse_iso_utc(str(row["last_promoted_at"]))
        if row["last_promoted_at"]
        else None,
        last_decay_check_at=parse_iso_utc(str(row["last_decay_check_at"]))
        if row["last_decay_check_at"]
        else None,
        milestone_progress=milestones_obj,
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
        format_iso_utc(record.timestamp),
        str(record.approval_id) if record.approval_id else None,
        record.details,
    )


def _row_to_record(row: dict[str, object]) -> TrustChangeRecord:
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
        timestamp=parse_iso_utc(str(row["timestamp"])),
        approval_id=NotBlankStr(str(approval_id)) if approval_id else None,
        details=str(row["details"]) if row["details"] is not None else "",
    )


class SQLiteTrustStateRepository:
    """SQLite upsert store for per-agent trust state.

    Args:
        db: An open ``aiosqlite.Connection``.
        write_context: Shared backend write context so writes serialise
            with sibling repos on the same connection.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def save(self, entity: TrustState, /) -> None:
        """Upsert one trust state keyed on ``agent_id``.

        Raises:
            QueryError: If the write fails.
        """
        async with self._write_context():
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                await self._db.execute(_STATE_UPSERT_SQL, _state_to_params(entity))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
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
            async with self._db.execute(
                f"SELECT {_STATE_COLUMNS} FROM trust_states WHERE agent_id = ?",  # noqa: S608
                (str(entity_id),),
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to read trust state"
            logger.warning(
                PERSISTENCE_TRUST_STATE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return self._deserialize_state(dict(row))

    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete the trust state for ``agent_id``.

        Returns:
            ``True`` iff a row existed.

        Raises:
            QueryError: If the delete fails.
        """
        async with self._write_context():
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                async with self._db.execute(
                    "DELETE FROM trust_states WHERE agent_id = ?",
                    (str(entity_id),),
                ) as cursor:
                    removed = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = "Failed to delete trust state"
                logger.warning(
                    PERSISTENCE_TRUST_STATE_DELETE_FAILED,
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
            async with self._db.execute(
                f"SELECT {_STATE_COLUMNS} FROM trust_states "  # noqa: S608
                "ORDER BY agent_id ASC LIMIT ? OFFSET ?",
                (limit, offset),
            ) as cursor:
                rows = list(await cursor.fetchall())
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list trust states"
            logger.warning(
                PERSISTENCE_TRUST_STATE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._deserialize_state(dict(r)) for r in rows)

    def _deserialize_state(self, row: dict[str, object]) -> TrustState:
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

    async def _safe_rollback(self) -> None:
        """Best-effort rollback on the shared connection."""
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            logger.warning(
                PERSISTENCE_TRUST_STATE_SAVE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                rollback_failed=True,
            )


class SQLiteTrustChangeHistoryRepository:
    """SQLite append-only audit trail of trust level transitions.

    Args:
        db: An open ``aiosqlite.Connection``.
        write_context: Shared backend write context.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def append(self, event: TrustChangeRecord, /) -> None:
        """Append one immutable change record.

        Raises:
            QueryError: If the write fails.
        """
        async with self._write_context():
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                await self._db.execute(_HISTORY_INSERT_SQL, _record_to_params(event))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
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
            sql += " WHERE agent_id = ?"
            params.append(str(filter_spec.agent_id))
        sql += " ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = list(await cursor.fetchall())
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query trust change history"
            logger.warning(
                PERSISTENCE_TRUST_CHANGE_HISTORY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            records = tuple(_row_to_record(dict(r)) for r in rows)
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
        async with self._write_context():
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                async with self._db.execute(
                    "DELETE FROM trust_change_history WHERE timestamp < ?",
                    (format_iso_utc(threshold),),
                ) as cursor:
                    removed = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = "Failed to purge trust change history"
                logger.warning(
                    PERSISTENCE_TRUST_CHANGE_HISTORY_QUERY_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return removed

    async def _safe_rollback(self) -> None:
        """Best-effort rollback on the shared connection."""
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            logger.warning(
                PERSISTENCE_TRUST_CHANGE_HISTORY_APPEND_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                rollback_failed=True,
            )
