"""SQLite repository implementation for flight-recorder frames."""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

import contextlib
import json
import sqlite3

import aiosqlite
from pydantic import AwareDatetime, ValidationError

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
from synthorg.persistence._shared import normalize_utc, parse_iso_utc
from synthorg.persistence._shared.datetime_marshaller import format_iso_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrame,
    FlightRecorderFrameAggregate,
    FlightRecorderFrameFilterSpec,
)
from synthorg.persistence.sqlite._shared import (
    WriteContext,
    is_unique_constraint_error,
)

logger = get_logger(__name__)

_COLUMNS = (
    "id, execution_id, task_id, agent_id, turn_index, timestamp, "
    "prompt_summary, response_summary, decision, tool_calls, "
    "input_tokens, output_tokens, cost, status, intervention_kind"
)

_INSERT_SQL = f"""\
INSERT INTO flight_recorder_frames ({_COLUMNS}) VALUES (
    :id, :execution_id, :task_id, :agent_id, :turn_index, :timestamp,
    :prompt_summary, :response_summary, :decision, :tool_calls,
    :input_tokens, :output_tokens, :cost, :status, :intervention_kind
)"""


class SQLiteFlightRecorderFrameRepository:
    """SQLite implementation of the ``FlightRecorderFrameRepository`` protocol.

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

    async def append(self, frame: FlightRecorderFrame) -> None:
        """Persist one frame (append-only; a duplicate id is a violation).

        Raises:
            QueryError: If the database query fails.
            DuplicateRecordError: If a row with the same key already exists.
        """
        async with self._write_context():
            try:
                await self._db.execute(_INSERT_SQL, self._to_row(frame))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                if is_unique_constraint_error(exc):
                    msg = f"Flight recorder frame {frame.id!r} already exists"
                    logger.warning(
                        PERSISTENCE_FLIGHT_RECORDER_SAVE_FAILED,
                        frame_id=frame.id,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    raise DuplicateRecordError(msg) from exc
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

        A duplicate id anywhere in the batch (or a UNIQUE
        (execution_id, turn_index) collision) rolls the entire batch
        back and surfaces as ``DuplicateRecordError``; any other backend
        error rolls back and surfaces as ``QueryError``. Empty batches
        are a no-op.

        Raises:
            QueryError: If the database query fails.
            DuplicateRecordError: If a row with the same key already exists.
        """
        if not frames:
            return
        rows = [self._to_row(frame) for frame in frames]
        async with self._write_context():
            try:
                await self._db.executemany(_INSERT_SQL, rows)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                if is_unique_constraint_error(exc):
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
            Tuple of (items, next_cursor) for paginated iteration.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_FLIGHT_RECORDER_QUERY_FAILED
        )
        where, params = self._build_where(filter_spec)
        sql = (
            f"SELECT {_COLUMNS} FROM flight_recorder_frames WHERE {where} "
            "ORDER BY turn_index DESC, timestamp DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        where, base_params = self._build_where(filter_spec)
        sql = (
            "SELECT "
            "COALESCE(SUM(cost), 0) AS total_cost, "
            "COALESCE(MAX(turn_index), 0) AS max_turn_index, "
            f"(SELECT timestamp FROM flight_recorder_frames WHERE {where} "
            "ORDER BY timestamp DESC, turn_index DESC LIMIT 1) AS latest_timestamp, "
            f"(SELECT execution_id FROM flight_recorder_frames WHERE {where} "
            "ORDER BY timestamp DESC, turn_index DESC LIMIT 1) "
            "AS latest_execution_id "
            f"FROM flight_recorder_frames WHERE {where}"
        )
        # Three subqueries share identical WHERE bindings; SQLite uses
        # positional ``?`` so the same params list must be repeated for
        # each subquery (latest_timestamp, latest_execution_id, outer
        # FROM filter).
        params = [*base_params, *base_params, *base_params]
        try:
            cursor = await self._db.execute(sql, params)
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to aggregate flight recorder frames"
            logger.warning(
                PERSISTENCE_FLIGHT_RECORDER_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return FlightRecorderFrameAggregate()
        row_dict = dict(row)
        latest_ts_raw = row_dict.get("latest_timestamp")
        latest_ts = (
            parse_iso_utc(str(latest_ts_raw)) if latest_ts_raw is not None else None
        )
        latest_exec = row_dict.get("latest_execution_id")
        return FlightRecorderFrameAggregate(
            total_cost=float(row_dict.get("total_cost", 0) or 0),
            max_turn_index=int(row_dict.get("max_turn_index", 0) or 0),
            latest_timestamp=latest_ts,
            latest_execution_id=NotBlankStr(latest_exec) if latest_exec else None,
        )

    async def purge_before(self, threshold: AwareDatetime) -> int:
        """Delete frames with ``timestamp < threshold``.

        ``threshold`` is an ``AwareDatetime`` so naive values cannot
        slip through silently.

        Returns:
            Numeric result of the operation.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM flight_recorder_frames WHERE timestamp < ?",
                    (format_iso_utc(normalize_utc(threshold)),),
                )
                count = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
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
            The matching collection.
        """
        conditions: list[str] = []
        params: list[object] = []
        if filter_spec.execution_id is not None:
            conditions.append("execution_id = ?")
            params.append(filter_spec.execution_id)
        if filter_spec.task_id is not None:
            conditions.append("task_id = ?")
            params.append(filter_spec.task_id)
        if filter_spec.agent_id is not None:
            conditions.append("agent_id = ?")
            params.append(filter_spec.agent_id)
        if filter_spec.turn_index_min is not None:
            conditions.append("turn_index >= ?")
            params.append(filter_spec.turn_index_min)
        if filter_spec.turn_index_max is not None:
            conditions.append("turn_index <= ?")
            params.append(filter_spec.turn_index_max)
        where = " AND ".join(conditions) if conditions else "1=1"
        return where, params

    def _to_row(self, frame: FlightRecorderFrame) -> dict[str, object]:
        """Flatten a frame into a row dict (tool_calls JSON-encoded).

        Returns:
            Result of type ``dict[str, object]``.
        """
        data = frame.model_dump(mode="json")
        data["tool_calls"] = json.dumps(list(frame.tool_calls))
        data["timestamp"] = format_iso_utc(normalize_utc(frame.timestamp))
        return data

    def _row_to_model(self, row: dict[str, object]) -> FlightRecorderFrame:
        """Convert a database row to a ``FlightRecorderFrame`` model.

        Raises:
            QueryError: If the row cannot be deserialized.

        Returns:
            Result of type ``FlightRecorderFrame``.
        """
        try:
            raw_tool_calls = row.get("tool_calls")
            decoded = (
                tuple(json.loads(raw_tool_calls))
                if isinstance(raw_tool_calls, str)
                else raw_tool_calls
            )
            return FlightRecorderFrame.model_validate({**row, "tool_calls": decoded})
        except (ValidationError, json.JSONDecodeError) as exc:
            msg = f"Failed to deserialize flight recorder frame {row.get('id')!r}"
            logger.warning(
                PERSISTENCE_FLIGHT_RECORDER_DESERIALIZE_FAILED,
                frame_id=row.get("id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
