"""SQLite repository implementation for flight-recorder frames."""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

import contextlib
import json
import sqlite3
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_FLIGHT_RECORDER_DELETE_FAILED,
    PERSISTENCE_FLIGHT_RECORDER_DESERIALIZE_FAILED,
    PERSISTENCE_FLIGHT_RECORDER_QUERY_FAILED,
    PERSISTENCE_FLIGHT_RECORDER_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared.datetime_marshaller import format_iso_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrame,
    FlightRecorderFrameFilterSpec,
)
from synthorg.persistence.sqlite._shared import (
    WriteContext,
    is_unique_constraint_error,
)

if TYPE_CHECKING:
    from datetime import datetime

logger = get_logger(__name__)

_COLUMNS = (
    "id, execution_id, task_id, agent_id, turn_index, timestamp, "
    "prompt_summary, response_summary, decision, tool_calls, "
    "input_tokens, output_tokens, cost, status, intervention_kind"
)


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
        """Persist one frame (append-only; a duplicate id is a violation)."""
        async with self._write_context():
            try:
                data = self._to_row(frame)
                await self._db.execute(
                    f"""\
INSERT INTO flight_recorder_frames ({_COLUMNS}) VALUES (
    :id, :execution_id, :task_id, :agent_id, :turn_index, :timestamp,
    :prompt_summary, :response_summary, :decision, :tool_calls,
    :input_tokens, :output_tokens, :cost, :status, :intervention_kind
)""",
                    data,
                )
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

    async def query(
        self,
        filter_spec: FlightRecorderFrameFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[FlightRecorderFrame, ...]:
        """Return frames matching the filter, newest-first by turn index."""
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_FLIGHT_RECORDER_QUERY_FAILED
        )
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

    async def purge_before(self, threshold: datetime) -> int:
        """Delete frames with ``timestamp < threshold``.

        ``threshold`` must be timezone-aware; a naive value would make
        the cut-off ambiguous against UTC-formatted stored timestamps.
        """
        if threshold.tzinfo is None:
            msg = f"threshold must be timezone-aware, got naive {threshold!r}"
            logger.warning(
                PERSISTENCE_FLIGHT_RECORDER_DELETE_FAILED,
                error="naive_threshold",
                error_type="ValueError",
            )
            raise QueryError(msg)
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM flight_recorder_frames WHERE timestamp < ?",
                    (format_iso_utc(threshold),),
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

    def _to_row(self, frame: FlightRecorderFrame) -> dict[str, object]:
        """Flatten a frame into a row dict (tool_calls JSON-encoded)."""
        data = frame.model_dump(mode="json")
        data["tool_calls"] = json.dumps(list(frame.tool_calls))
        data["timestamp"] = format_iso_utc(normalize_utc(frame.timestamp))
        return data

    def _row_to_model(self, row: dict[str, object]) -> FlightRecorderFrame:
        """Convert a database row to a ``FlightRecorderFrame`` model.

        Raises:
            QueryError: If the row cannot be deserialized.
        """
        try:
            raw_tool_calls = row.get("tool_calls")
            if isinstance(raw_tool_calls, str):
                row["tool_calls"] = tuple(json.loads(raw_tool_calls))
            return FlightRecorderFrame.model_validate(row)
        except (ValidationError, json.JSONDecodeError) as exc:
            msg = f"Failed to deserialize flight recorder frame {row.get('id')!r}"
            logger.warning(
                PERSISTENCE_FLIGHT_RECORDER_DESERIALIZE_FAILED,
                frame_id=row.get("id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
