"""Flight-recorder query + seek service for the cockpit replay scrubber.

The persisted frame store is the authoritative replay source: this
service serves the scrubber timeline (newest-first frames) and a
"seek to turn N" reconstruction (frames 1..N ascending plus cumulative
cost) entirely from frames, with no dependency on the observability
event log.
"""

from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.cockpit import FLIGHT_RECORDER_SEEK
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrame,
    FlightRecorderFrameFilterSpec,
    FlightRecorderFrameRepository,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = get_logger(__name__)

#: Upper bound on frames a single seek reconstructs, so a pathological
#: turn index cannot pull an unbounded page from the store.
_MAX_SEEK_FRAMES: Final[int] = 1000


def _sum_costs(costs: Iterable[float]) -> float:
    """Sum costs known to share one budget currency by construction."""
    return sum(costs)  # lint-allow: currency-aggregation -- single budget


class ReplaySeekView(BaseModel):
    """Reconstructed scrubber state at a target turn.

    ``frames`` are ascending by turn index from turn 1 up to and
    including ``turn_index``; ``current_frame`` is the frame at
    ``turn_index`` (``None`` when that turn was never recorded).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    execution_id: NotBlankStr = Field(description="Execution being replayed")
    turn_index: int = Field(ge=1, description="Target turn index")
    frames: tuple[FlightRecorderFrame, ...] = Field(
        default=(),
        description="Frames 1..turn_index, ascending",
    )
    current_frame: FlightRecorderFrame | None = Field(
        default=None,
        description="Frame at turn_index, when recorded",
    )
    cumulative_cost: float = Field(
        default=0.0,
        ge=0.0,
        description="Summed cost of frames up to and including turn_index",
    )


class FlightRecorderService:
    """Query and seek over persisted flight-recorder frames."""

    def __init__(self, repository: FlightRecorderFrameRepository) -> None:
        self._repository = repository

    async def get_frames(
        self,
        execution_id: str,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[FlightRecorderFrame, ...]:
        """Return the scrubber timeline (newest-first) for an execution."""
        return await self._repository.query(
            FlightRecorderFrameFilterSpec(execution_id=NotBlankStr(execution_id)),
            limit=limit,
            offset=offset,
        )

    async def seek(self, execution_id: str, turn_index: int) -> ReplaySeekView:
        """Reconstruct scrubber state at ``turn_index`` from frames 1..N."""
        frames = await self._repository.query(
            FlightRecorderFrameFilterSpec(
                execution_id=NotBlankStr(execution_id),
                turn_index_min=1,
                turn_index_max=turn_index,
            ),
            limit=_MAX_SEEK_FRAMES,
        )
        ascending = tuple(sorted(frames, key=lambda f: f.turn_index))
        current = next(
            (f for f in ascending if f.turn_index == turn_index),
            None,
        )
        cumulative = _sum_costs(f.cost for f in ascending)
        logger.debug(
            FLIGHT_RECORDER_SEEK,
            execution_id=execution_id,
            turn_index=turn_index,
            frames_loaded=len(ascending),
        )
        return ReplaySeekView(
            execution_id=NotBlankStr(execution_id),
            turn_index=turn_index,
            frames=ascending,
            current_frame=current,
            cumulative_cost=cumulative,
        )
