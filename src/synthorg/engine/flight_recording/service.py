"""Flight-recorder query + seek service for the cockpit replay scrubber.

The persisted frame store is the authoritative replay source: this
service serves the scrubber timeline (newest-first frames) and a
"seek to turn N" reconstruction entirely from frames, with no
dependency on the observability event log.
"""

from typing import Final

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

logger = get_logger(__name__)

#: Upper bound on frames a single seek reconstructs, so a pathological
#: turn index cannot pull an unbounded page from the store. When a run
#: exceeds this many turns the seek view sets ``truncated=True`` and the
#: returned ``frames`` carry only the most recent window
#: (``turn_index_max - _MAX_SEEK_FRAMES + 1 .. turn_index_max``);
#: ``cumulative_cost`` stays accurate across the whole run because it
#: comes from an unbounded SQL aggregate, not from summing the windowed
#: frames.
_MAX_SEEK_FRAMES: Final[int] = 1000


class ReplaySeekView(BaseModel):
    """Reconstructed scrubber state at a target turn.

    ``frames`` are ascending by turn index. For runs with at most
    ``_MAX_SEEK_FRAMES`` turns the array spans turns
    ``1..turn_index`` exactly; for larger runs the array carries the
    most recent ``_MAX_SEEK_FRAMES`` turns up to ``turn_index`` and the
    ``truncated`` flag is ``True`` so callers can render a "partial
    reconstruction" affordance instead of silently showing an
    incomplete prefix. ``current_frame`` is always the frame at
    ``turn_index`` when one was recorded. ``cumulative_cost`` is the
    SQL ``SUM(cost)`` across the full filtered set, not just the
    windowed frames, so the figure stays accurate even when truncated.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    execution_id: NotBlankStr = Field(description="Execution being replayed")
    turn_index: int = Field(ge=1, description="Target turn index")
    frames: tuple[FlightRecorderFrame, ...] = Field(
        default=(),
        description=(
            "Frames ascending up to ``turn_index``; windowed to the most"
            " recent ``_MAX_SEEK_FRAMES`` turns when ``truncated`` is True"
        ),
    )
    current_frame: FlightRecorderFrame | None = Field(
        default=None,
        description="Frame at turn_index, when recorded",
    )
    cumulative_cost: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Summed cost across every turn up to and including"
            " ``turn_index``; uses an unbounded SQL aggregate so it"
            " stays accurate when ``frames`` is windowed"
        ),
    )
    truncated: bool = Field(
        default=False,
        description=(
            "True when the run exceeded ``_MAX_SEEK_FRAMES`` and the"
            " returned ``frames`` are a windowed tail rather than the"
            " full prefix; callers should surface this to operators so"
            " a partial scrubber reconstruction is never silent"
        ),
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
        """Reconstruct scrubber state at ``turn_index``.

        Returns frames ascending up to ``turn_index`` plus a cumulative
        cost (taken from an unbounded SQL aggregate, so the figure is
        not capped by ``_MAX_SEEK_FRAMES``). When the requested
        ``turn_index`` exceeds the seek cap the returned ``frames`` are
        the most recent ``_MAX_SEEK_FRAMES`` turns and ``truncated`` is
        ``True``; ``cumulative_cost`` and ``current_frame`` remain
        accurate.

        Returns:
            A :class:`ReplaySeekView` carrying the (possibly
            truncated) frame window, cumulative cost up to
            ``turn_index``, and the current frame.
        """
        filter_spec = FlightRecorderFrameFilterSpec(
            execution_id=NotBlankStr(execution_id),
            turn_index_min=1,
            turn_index_max=turn_index,
        )
        windowed_frames = await self._repository.query(
            filter_spec,
            limit=_MAX_SEEK_FRAMES,
        )
        ascending = tuple(sorted(windowed_frames, key=lambda f: f.turn_index))
        aggregate = await self._repository.get_aggregate(filter_spec)
        # Derive truncation from the actual recorded max turn (the
        # aggregate's ``max_turn_index``), not from the operator-supplied
        # ``turn_index``: a scrubber that seeks to turn 2000 in a run
        # that only recorded 50 frames is NOT truncated, even though
        # 2000 > _MAX_SEEK_FRAMES.
        truncated = aggregate.max_turn_index > _MAX_SEEK_FRAMES
        cumulative = aggregate.total_cost
        current = next(
            (f for f in ascending if f.turn_index == turn_index),
            None,
        )
        logger.debug(
            FLIGHT_RECORDER_SEEK,
            execution_id=execution_id,
            turn_index=turn_index,
            frames_loaded=len(ascending),
            truncated=truncated,
        )
        return ReplaySeekView(
            execution_id=NotBlankStr(execution_id),
            turn_index=turn_index,
            frames=ascending,
            current_frame=current,
            cumulative_cost=cumulative,
            truncated=truncated,
        )
