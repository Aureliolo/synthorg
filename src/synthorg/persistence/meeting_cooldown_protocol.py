"""Meeting cooldown persistence protocol and record.

The ``MeetingScheduler`` enforces a ``min_interval_seconds`` cooldown
between consecutive triggers of the same meeting type, tracking the
last-triggered wall-clock timestamp per meeting type. Pre-WP-1 this
lived in a plain ``dict[str, float]`` keyed by meeting-type name and
was lost on restart, allowing a recurring meeting to fire again
immediately after a deploy that happened during the cooldown window.

This repository persists one row per meeting type. The scheduler:

* Hydrates the dict at start via :meth:`load_all`.
* Calls :meth:`upsert` after every trigger with the wall-clock
  timestamp.
* Uses wall-clock (``Clock.now()``) rather than ``time.monotonic()`` so
  the persisted value remains meaningful across process boundaries.
"""

from datetime import datetime  # noqa: TC003 -- runtime needed by Pydantic field
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001


class MeetingCooldownRecord(BaseModel):
    """Persisted last-triggered timestamp for one meeting type.

    Attributes:
        meeting_type_name: Meeting type whose cooldown we are tracking.
        last_triggered_at: UTC wall-clock timestamp of the most recent
            successful trigger. Clock-skew tolerant via ``max(0,
            elapsed)`` in the scheduler.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    meeting_type_name: NotBlankStr = Field(
        description="Meeting type whose cooldown we track"
    )
    last_triggered_at: datetime = Field(
        description="UTC wall-clock timestamp of the most recent trigger"
    )


@runtime_checkable
class MeetingCooldownRepository(Protocol):
    """Persistence interface for meeting cooldown timestamps.

    Implementations live under ``persistence/sqlite/`` and
    ``persistence/postgres/``.
    """

    async def upsert(self, record: MeetingCooldownRecord) -> None:
        """Insert or replace the cooldown row for one meeting type.

        Args:
            record: Cooldown record to persist.

        Raises:
            QueryError: If the underlying write fails.
        """
        ...

    async def load_all(self) -> tuple[MeetingCooldownRecord, ...]:
        """Load every cooldown row (called once at scheduler start).

        Returns:
            All persisted cooldown rows, order unspecified.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...

    async def delete(self, meeting_type_name: NotBlankStr) -> bool:
        """Delete the cooldown row for one meeting type.

        Args:
            meeting_type_name: Meeting type whose cooldown to remove.

        Returns:
            ``True`` if a row was deleted, ``False`` if no row existed.

        Raises:
            QueryError: If the underlying DELETE fails.
        """
        ...
