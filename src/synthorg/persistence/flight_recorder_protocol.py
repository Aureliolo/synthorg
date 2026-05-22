"""Flight-recorder frame model and repository protocol.

A ``FlightRecorderFrame`` captures one completed agent turn with enough
redacted content for the mission-control cockpit to replay a run
step-by-step. The frame store is the authoritative replay source: the
scrubber timeline and per-turn detail come entirely from persisted
frames, independent of the observability event log.
"""

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.enums import (  # noqa: TC001 -- Pydantic field types
    InterventionKind,
    TaskStatus,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, AppendOnlyRepository

__all__ = [
    "FlightRecorderFrame",
    "FlightRecorderFrameAggregate",
    "FlightRecorderFrameFilterSpec",
    "FlightRecorderFrameRepository",
]


class FlightRecorderFrame(BaseModel):
    """One recorded agent turn for cockpit replay.

    Content fields (``prompt_summary`` / ``response_summary``) are
    redacted and length-bounded at the recording boundary; this model
    stores them verbatim. ``execution_id`` keys the run timeline;
    ``task_id`` + ``agent_id`` let interventions target the right work
    without a separate mapping lookup.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique frame identifier",
    )
    execution_id: NotBlankStr = Field(description="Execution run identifier")
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Task the agent was working on, when known",
    )
    agent_id: NotBlankStr = Field(description="Agent that produced the turn")
    turn_index: int = Field(ge=1, description="1-based turn index within the run")
    timestamp: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the turn completed",
    )
    prompt_summary: str | None = Field(
        default=None,
        description="Redacted, length-bounded prompt summary",
    )
    response_summary: str | None = Field(
        default=None,
        description="Redacted, length-bounded model response summary",
    )
    decision: str | None = Field(
        default=None,
        description="Classified turn outcome (e.g. tool_call, completed)",
    )
    tool_calls: tuple[str, ...] = Field(
        default=(),
        description="Tool names invoked during the turn",
    )
    input_tokens: int = Field(default=0, ge=0, description="Prompt tokens")
    output_tokens: int = Field(default=0, ge=0, description="Completion tokens")
    cost: float = Field(default=0.0, ge=0, description="Turn cost")
    status: TaskStatus = Field(description="Task status at turn completion")
    intervention_kind: InterventionKind | None = Field(
        default=None,
        description="Operator intervention recorded on this turn, if any",
    )


class FlightRecorderFrameFilterSpec(BaseModel):
    """Filter spec for ``FlightRecorderFrameRepository.query``."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    execution_id: NotBlankStr | None = Field(
        default=None,
        description="Filter to a single execution",
    )
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Filter to a single task",
    )
    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Filter to a single agent",
    )
    turn_index_min: int | None = Field(
        default=None,
        ge=1,
        description="Inclusive lower bound on turn index",
    )
    turn_index_max: int | None = Field(
        default=None,
        ge=1,
        description="Inclusive upper bound on turn index",
    )


class FlightRecorderFrameAggregate(BaseModel):
    """Aggregate stats over a filtered frame set, computed in one query.

    ``latest_timestamp`` and ``latest_execution_id`` come from the same
    row (the one with the most recent ``timestamp`` under the filter,
    with ``turn_index`` as a tiebreaker) so callers can identify the
    most recent activity in a single round-trip. Ordering by timestamp
    first matches the semantic meaning of "latest activity" -- a frame
    at turn 5 written 30s after turn 6 (clock skew, resumed-run
    interleaving) is the *more recent* activity even if turn 6 has a
    higher index. ``total_cost`` and ``max_turn_index`` are SQL
    aggregates over the entire filtered set, not just the rows that fit
    in a page.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    total_cost: float = Field(
        default=0.0,
        ge=0.0,
        description="Sum of cost across all matching frames",
    )
    max_turn_index: int = Field(
        default=0,
        ge=0,
        description=(
            "Maximum ``turn_index`` across matching frames; 0 when the"
            " filter matches no rows"
        ),
    )
    latest_timestamp: AwareDatetime | None = Field(
        default=None,
        description=(
            "Timestamp of the latest matching frame, ordered by"
            " (timestamp DESC, turn_index DESC); ``None`` when empty"
        ),
    )
    latest_execution_id: NotBlankStr | None = Field(
        default=None,
        description=(
            "Execution id of the latest matching frame, ordered by"
            " (timestamp DESC, turn_index DESC); ``None`` when empty"
        ),
    )


@runtime_checkable
class FlightRecorderFrameRepository(
    AppendOnlyRepository["FlightRecorderFrame", FlightRecorderFrameFilterSpec],
    Protocol,
):
    """Append-only persistence for flight-recorder frames.

    Composes :class:`AppendOnlyRepository`: ``append`` writes one
    immutable frame, ``query`` returns frames newest-first under a
    filter, and ``purge_before`` enforces retention. ``append_many`` and
    ``get_aggregate`` are bespoke methods admitted under
    `ADR-0001 D7 <../decisions/0001-repository-protocol-consolidation.md>`_
    as real perf optimisations: batched-frame finalisation avoids N
    one-row transactions on every run, and the cockpit dashboard needs
    a single-query aggregate to avoid N+1 query patterns when summarising
    activity across many in-flight tasks.
    """

    async def append(  # pyright: ignore[reportIncompatibleMethodOverride] -- domain-specific param name
        self, frame: FlightRecorderFrame
    ) -> None:
        """Persist one frame (append-only; a duplicate id is a violation)."""
        ...

    async def append_many(self, frames: tuple[FlightRecorderFrame, ...]) -> None:
        """Persist a batch of frames in one transaction.

        A duplicate id anywhere in the batch raises
        ``DuplicateRecordError`` and rolls the entire batch back so the
        store never reflects a partial finalise; on any other backend
        error the batch is rolled back and ``QueryError`` is raised.
        An empty tuple is a no-op.
        """
        ...

    async def query(
        self,
        filter_spec: FlightRecorderFrameFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[FlightRecorderFrame, ...]:
        """Return frames matching the filter, newest-first (by turn index)."""
        ...

    async def get_aggregate(
        self,
        filter_spec: FlightRecorderFrameFilterSpec,
    ) -> FlightRecorderFrameAggregate:
        """Return aggregate stats for matching frames in one query.

        The aggregate is unbounded by pagination; ``total_cost`` and
        ``max_turn_index`` cover every matching row, so consumers can
        compute cumulative cost or latest turn without paging through
        the table. ``latest_timestamp`` and ``latest_execution_id`` are
        taken from the single most-recent row (by turn_index then
        timestamp). An empty match returns an all-zero / all-``None``
        aggregate.
        """
        ...

    async def purge_before(self, threshold: AwareDatetime) -> int:
        """Delete frames with ``timestamp < threshold``. Returns rows removed.

        ``threshold`` must be timezone-aware (an ``AwareDatetime``);
        passing a naive value is a contract violation and is rejected
        at the persistence boundary so the cut-off cannot drift with
        the session timezone.
        """
        ...
