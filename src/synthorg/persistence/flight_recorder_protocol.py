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


@runtime_checkable
class FlightRecorderFrameRepository(
    AppendOnlyRepository["FlightRecorderFrame", FlightRecorderFrameFilterSpec],
    Protocol,
):
    """Append-only persistence for flight-recorder frames.

    Composes :class:`AppendOnlyRepository`: ``append`` writes one
    immutable frame, ``query`` returns frames newest-first under a
    filter, and ``purge_before`` enforces retention. No bespoke methods;
    the cockpit reconstructs ascending turn order in the service layer.
    """

    async def append(self, frame: FlightRecorderFrame) -> None:
        """Persist one frame (append-only; a duplicate id is a violation)."""
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

    async def purge_before(self, threshold: datetime) -> int:
        """Delete frames with ``timestamp < threshold``. Returns rows removed."""
        ...
