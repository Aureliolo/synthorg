"""Postgres implementation of the ``FlightRecorderFrameRepository`` protocol.

Postgres sibling of ``persistence/sqlite/flight_recorder_repo.py``.
``tool_calls`` is stored as native JSONB and ``timestamp`` as TIMESTAMPTZ.
"""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
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
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrame,
    FlightRecorderFrameFilterSpec,
)

if TYPE_CHECKING:
    from datetime import datetime

    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_COLUMNS = (
    "id, execution_id, task_id, agent_id, turn_index, timestamp, "
    "prompt_summary, response_summary, decision, tool_calls, "
    "input_tokens, output_tokens, cost, status, intervention_kind"
)


class PostgresFlightRecorderFrameRepository:
    """Postgres implementation of the ``FlightRecorderFrameRepository`` protocol.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, frame: FlightRecorderFrame) -> None:
        """Persist one frame (append-only; a duplicate id is a violation)."""
        try:
            data = frame.model_dump(mode="json")
            data["tool_calls"] = Jsonb(list(frame.tool_calls))
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    f"""\
INSERT INTO flight_recorder_frames ({_COLUMNS}) VALUES (
    %(id)s, %(execution_id)s, %(task_id)s, %(agent_id)s, %(turn_index)s,
    %(timestamp)s, %(prompt_summary)s, %(response_summary)s, %(decision)s,
    %(tool_calls)s, %(input_tokens)s, %(output_tokens)s, %(cost)s,
    %(status)s, %(intervention_kind)s
)""",
                    data,
                )
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
        sql = (
            f"SELECT {_COLUMNS} FROM flight_recorder_frames WHERE {where} "
            "ORDER BY turn_index DESC, timestamp DESC LIMIT %s OFFSET %s"
        )
        params.extend([limit, offset])
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
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

    async def purge_before(self, threshold: datetime) -> int:
        """Delete frames with ``timestamp < threshold``.

        ``threshold`` must be timezone-aware; a naive value would make
        the cut-off depend on the backend's session timezone.
        """
        if threshold.tzinfo is None:
            msg = f"threshold must be timezone-aware, got naive {threshold!r}"
            logger.warning(
                PERSISTENCE_FLIGHT_RECORDER_DELETE_FAILED,
                error="naive_threshold",
                error_type="ValueError",
            )
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

    def _row_to_model(self, row: dict[str, object]) -> FlightRecorderFrame:
        """Convert a database row to a ``FlightRecorderFrame`` model.

        ``tool_calls`` comes back from Postgres JSONB as a Python list;
        the model expects a tuple, so coerce before validation.

        Raises:
            QueryError: If the row cannot be deserialized.
        """
        try:
            raw_tool_calls = row.get("tool_calls")
            if isinstance(raw_tool_calls, list):
                row["tool_calls"] = tuple(raw_tool_calls)
            return FlightRecorderFrame.model_validate(row)
        except ValidationError as exc:
            msg = f"Failed to deserialize flight recorder frame {row.get('id')!r}"
            logger.warning(
                PERSISTENCE_FLIGHT_RECORDER_DESERIALIZE_FAILED,
                frame_id=row.get("id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
