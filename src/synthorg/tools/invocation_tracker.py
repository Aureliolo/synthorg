"""In-memory tool invocation tracker with size-cap eviction.

Records :class:`ToolInvocationRecord` entries from tool executions and
provides filtered queries for the activity timeline.  When the record
count exceeds ``max_records``, oldest entries are evicted (FIFO).
"""

import asyncio
from collections import deque
from typing import TYPE_CHECKING, Final

from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    TOOL_INVOCATION_EVICTED,
    TOOL_INVOCATION_RECORDED,
    TOOL_INVOCATION_TIME_RANGE_INVALID,
    TOOL_INVOCATION_TRACKER_CLEARED,
    TOOL_INVOCATIONS_QUERIED,
)

if TYPE_CHECKING:
    from datetime import datetime

    from synthorg.tools.invocation_record import ToolInvocationRecord

logger = get_logger(__name__)

_DEFAULT_MAX_RECORDS: Final[int] = 10_000


class ToolInvocationTracker:
    """In-memory tool invocation tracking service with size-cap eviction.

    Records tool invocation outcomes and provides filtered queries
    for the activity timeline.  When record count exceeds
    ``max_records``, oldest entries are evicted (FIFO) via bounded
    ``deque``.

    Args:
        max_records: Maximum records before oldest are evicted.

    Raises:
        ValueError: If *max_records* < 1.
    """

    def __init__(
        self,
        *,
        max_records: int = _DEFAULT_MAX_RECORDS,
    ) -> None:
        if max_records < 1:
            msg = f"max_records must be >= 1, got {max_records}"
            raise ValueError(msg)
        self._records: deque[ToolInvocationRecord] = deque(
            maxlen=max_records,
        )
        self._lock: asyncio.Lock = asyncio.Lock()
        self._eviction_warned: bool = False

    def clear(self) -> None:
        """Reset all invocation records for test isolation."""
        cleared_count = len(self._records)
        self._records.clear()
        self._eviction_warned = False
        logger.info(
            TOOL_INVOCATION_TRACKER_CLEARED,
            cleared_count=cleared_count,
        )

    async def record(self, invocation: ToolInvocationRecord) -> None:
        """Append a tool invocation record.

        Args:
            invocation: Immutable invocation record to store.
        """
        async with self._lock:
            if not self._eviction_warned and len(self._records) == self._records.maxlen:
                logger.warning(
                    TOOL_INVOCATION_EVICTED,
                    max_records=self._records.maxlen,
                )
                self._eviction_warned = True
            self._records.append(invocation)
            logger.debug(
                TOOL_INVOCATION_RECORDED,
                agent_id=invocation.agent_id,
                tool_name=invocation.tool_name,
                is_success=invocation.is_success,
            )

    async def get_records(
        self,
        *,
        agent_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[ToolInvocationRecord, ...]:
        """Return filtered tool invocation records.

        Time semantics: ``start <= timestamp < end``.

        Args:
            agent_id: Filter by agent.
            start: Inclusive lower bound on ``timestamp``.
            end: Exclusive upper bound on ``timestamp``.

        Returns:
            Immutable tuple of matching records.

        Raises:
            ValueError: If both *start* and *end* are given and
                ``start >= end``.
        """
        _validate_time_range(start, end)
        logger.debug(
            TOOL_INVOCATIONS_QUERIED,
            agent_id=agent_id,
            start=start,
            end=end,
        )
        async with self._lock:
            snapshot = tuple(self._records)
        return tuple(
            r
            for r in snapshot
            if (agent_id is None or r.agent_id == agent_id)
            and (start is None or r.timestamp >= start)
            and (end is None or r.timestamp < end)
        )


# ── Module-level pure helpers ────────────────────────────────────


def _validate_time_range(
    start: datetime | None,
    end: datetime | None,
) -> None:
    """Raise ``ValueError`` if *start* >= *end* when both are given.

    Raises:
        ValueError: If an argument fails domain validation.
    """
    if start is not None and end is not None and start >= end:
        logger.warning(
            TOOL_INVOCATION_TIME_RANGE_INVALID,
            start=start.isoformat(),
            end=end.isoformat(),
        )
        msg = f"start ({start.isoformat()}) must be before end ({end.isoformat()})"
        raise ValueError(msg)
