"""Postgres implementation of the ``FlightRecorderFrameRepository`` protocol.

Postgres sibling of ``persistence/sqlite/flight_recorder_repo.py``.
``tool_calls`` is stored as native JSONB and ``timestamp`` as TIMESTAMPTZ.
"""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

from datetime import datetime
from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_FLIGHT_RECORDER_DELETE_FAILED,
    PERSISTENCE_FLIGHT_RECORDER_DESERIALIZE_FAILED,
    PERSISTENCE_FLIGHT_RECORDER_QUERY_FAILED,
    PERSISTENCE_FLIGHT_RECORDER_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrame,
    FlightRecorderFrameAggregate,
    FlightRecorderFrameFilterSpec,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_COLUMNS = (
    "id, execution_id, task_id, agent_id, turn_index, timestamp, "
    "prompt_summary, response_summary, decision, tool_calls, "
    "input_tokens, output_tokens, cost, status, intervention_kind"
)

_INSERT_SQL = f"""\
INSERT INTO flight_recorder_frames ({_COLUMNS}) VALUES (
    %(id)s, %(execution_id)s, %(task_id)s, %(agent_id)s, %(turn_index)s,
    %(timestamp)s, %(prompt_summary)s, %(response_summary)s, %(decision)s,
    %(tool_calls)s, %(input_tokens)s, %(output_tokens)s, %(cost)s,
    %(status)s, %(intervention_kind)s
)"""


class PostgresFlightRecorderFrameRepository:
    """Postgres implementation of the ``FlightRecorderFrameRepository`` protocol.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, frame: FlightRecorderFrame) -> None:
        """Persist one frame (append-only; a duplicate id is a violation).

        Raises:
            DuplicateRecordError: If a row with the same key already exists.
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_INSERT_SQL, self._to_row(frame))
                await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            msg = f"Flight recorder frame {frame.id!r} already exists"
            logger.warning(
                PERSISTENCE_FLIGHT_RECORDER_SAVE_FAILED,
                frame_id=frame.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise DuplicateRecordError(msg) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save flight recorder frame {frame.id!r}"
            logger.warning(
                PERSISTENCE_FLIGHT_RECORDER_SAVE_FAILED,
                frame_id=frame.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def append_many(self, frames: tuple[FlightRecorderFrame, ...]) -> None:
        """Append every frame in one transaction so a run finalises atomically.

        Uses ``executemany`` so the batch lands as one multi-row insert
        rather than N round-trips. A duplicate id (or
        ``UNIQUE (execution_id, turn_index)`` collision) rolls the entire
        batch back and surfaces as ``DuplicateRecordError``; any other
        backend error rolls back and surfaces as ``QueryError``. Empty
        batches are a no-op.

        Raises:
            DuplicateRecordError: If a row with the same key already exists.
            QueryError: If the database query fails.
        """
        if not frames:
            return
        rows = [self._to_row(frame) for frame in frames]
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.executemany(_INSERT_SQL, rows)
                await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            msg = (
                f"Flight recorder batch ({len(frames)} frames) failed:"
                " duplicate id or (execution_id, turn_index)"
            )
            logger.warning(
                PERSISTENCE_FLIGHT_RECORDER_SAVE_FAILED,
                batch_size=len(frames),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise DuplicateRecordError(msg) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save flight recorder batch ({len(frames)} frames)"
            logger.warning(
                PERSISTENCE_FLIGHT_RECORDER_SAVE_FAILED,
                batch_size=len(frames),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: FlightRecorderFrameFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[FlightRecorderFrame, ...]:
        """Return frames matching the filter, newest-first by turn index.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_FLIGHT_RECORDER_QUERY_FAILED
        )
        where, params = self._build_where(filter_spec)
        sql = (
            f"SELECT {_COLUMNS} FROM flight_recorder_frames WHERE {where} "
            "ORDER BY turn_index DESC, timestamp DESC LIMIT %s OFFSET %s"
        )
        all_params = [*params, limit, offset]
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, all_params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query flight recorder frames"
            logger.warning(
                PERSISTENCE_FLIGHT_RECORDER_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_model(dict(r)) for r in rows)

    async def get_aggregate(
        self,
        filter_spec: FlightRecorderFrameFilterSpec,
    ) -> FlightRecorderFrameAggregate:
        """Return aggregate stats over the matching frame set in one round-trip.

        Returns:
            Aggregate stats over the matching frame set. Counters default to zero and
            ``last_timestamp`` to ``None`` when the filter selects no frames.

        Raises:
            QueryError: If the database query fails.
        """
        where, params = self._build_where(filter_spec)
        sql = (
            "SELECT "
            "COALESCE(SUM(cost), 0)::float8 AS total_cost, "
            "COALESCE(MAX(turn_index), 0) AS max_turn_index, "
            f"(SELECT timestamp FROM flight_recorder_frames WHERE {where} "
            "ORDER BY timestamp DESC, turn_index DESC LIMIT 1) AS latest_timestamp, "
            f"(SELECT execution_id FROM flight_recorder_frames WHERE {where} "
            "ORDER BY timestamp DESC, turn_index DESC LIMIT 1) "
            "AS latest_execution_id "
            f"FROM flight_recorder_frames WHERE {where}"
        )
        # Three filtered scans share the same WHERE; psycopg uses
        # positional ``%s`` so the params list must repeat for each.
        all_params = [*params, *params, *params]
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, all_params)
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = "Failed to aggregate flight recorder frames"
            logger.warning(
                PERSISTENCE_FLIGHT_RECORDER_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return FlightRecorderFrameAggregate()
        latest_ts: datetime | None = row.get("latest_timestamp")
        if latest_ts is not None:
            latest_ts = normalize_utc(latest_ts)
        latest_exec = row.get("latest_execution_id")
        return FlightRecorderFrameAggregate(
            total_cost=float(row.get("total_cost", 0) or 0),
            max_turn_index=int(row.get("max_turn_index", 0) or 0),
            latest_timestamp=latest_ts,
            latest_execution_id=NotBlankStr(latest_exec) if latest_exec else None,
        )

    async def purge_before(self, threshold: datetime) -> int:
        """Delete frames with ``timestamp < threshold``.

        ``threshold`` must be timezone-aware; a naive value is rejected so the
        cut-off cannot drift silently with the session timezone.

        Returns:
            Number of rows deleted.

        Raises:
            QueryError: If ``threshold`` is naive, or the database query fails.
        """
        if threshold.tzinfo is None:
            msg = "threshold must be timezone-aware; a naive datetime is rejected"
            raise QueryError(msg)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM flight_recorder_frames WHERE timestamp < %s",
                    (normalize_utc(threshold),),
                )
                count = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge flight recorder frames by threshold"
            logger.warning(
                PERSISTENCE_FLIGHT_RECORDER_DELETE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return count

    def _build_where(
        self, filter_spec: FlightRecorderFrameFilterSpec
    ) -> tuple[str, list[object]]:
        """Build the WHERE clause + positional params for ``filter_spec``.

        Returns:
            ``(where_clause, params)`` where ``where_clause`` is the SQL fragment
            (without the leading ``WHERE``) and ``params`` is the matching positional
            parameter list.
        """
        conditions: list[str] = []
        params: list[object] = []
        if filter_spec.execution_id is not None:
            conditions.append("execution_id = %s")
            params.append(filter_spec.execution_id)
        if filter_spec.task_id is not None:
            conditions.append("task_id = %s")
            params.append(filter_spec.task_id)
        if filter_spec.agent_id is not None:
            conditions.append("agent_id = %s")
            params.append(filter_spec.agent_id)
        if filter_spec.turn_index_min is not None:
            conditions.append("turn_index >= %s")
            params.append(filter_spec.turn_index_min)
        if filter_spec.turn_index_max is not None:
            conditions.append("turn_index <= %s")
            params.append(filter_spec.turn_index_max)
        where = " AND ".join(conditions) if conditions else "TRUE"
        return where, params

    def _to_row(self, frame: FlightRecorderFrame) -> dict[str, object]:
        """Flatten a frame into a row dict (tool_calls wrapped as JSONB).

        Returns:
            Result of type ``dict[str, object]``.
        """
        data = frame.model_dump(mode="json")
        data["tool_calls"] = Jsonb(list(frame.tool_calls))
        return data

    def _row_to_model(self, row: DictRow) -> FlightRecorderFrame:
        """Convert a database row to a ``FlightRecorderFrame`` model.

        ``tool_calls`` comes back from Postgres JSONB as a Python list;
        the model expects a tuple, so coerce before validation.

        Raises:
            QueryError: If the row cannot be deserialized.

        Returns:
            Result of type ``FlightRecorderFrame``.
        """
        try:
            raw_tool_calls = row.get("tool_calls")
            decoded = (
                tuple(raw_tool_calls)
                if isinstance(raw_tool_calls, list)
                else raw_tool_calls
            )
            return FlightRecorderFrame.model_validate({**row, "tool_calls": decoded})
        except ValidationError as exc:
            msg = f"Failed to deserialize flight recorder frame {row.get('id')!r}"
            logger.warning(
                PERSISTENCE_FLIGHT_RECORDER_DESERIALIZE_FAILED,
                frame_id=row.get("id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
