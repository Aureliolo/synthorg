# module-kind: code
"""The meeting shape a surface renders: analytics, plus everyone it names."""

import copy
from collections.abc import Mapping
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from synthorg.communication.meeting.models import MeetingRecord
from synthorg.core.types import NotBlankStr


class MeetingResponse(MeetingRecord):
    """Meeting record enriched with per-participant analytics.

    Attributes:
        token_usage_by_participant: Total tokens per agent.
        contribution_rank: Agent IDs sorted by total tokens (desc).
        meeting_duration_seconds: Duration in seconds (populated when
            minutes are present, ``None`` otherwise).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    token_usage_by_participant: dict[str, int] = Field(
        default_factory=dict,
        description="Total tokens consumed per participant",
    )
    contribution_rank: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Agent IDs sorted by contribution (descending)",
    )
    meeting_duration_seconds: float | None = Field(
        default=None,
        ge=0.0,
        description="Meeting duration in seconds (null if no minutes)",
    )
    participant_names: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Display name of every agent this meeting references, keyed by id:"
            " speakers, agenda presenters and action-item assignees. An id"
            " absent from the map is one nothing could name, which the surface"
            " words itself rather than printing the key"
        ),
    )

    @model_validator(mode="after")
    def _deep_copy_participant_names(self) -> Self:
        """Deep-copy participant_names so the frozen model cannot be aliased.

        Returns:
            The instance with ``participant_names`` deep-copied.
        """
        object.__setattr__(
            self, "participant_names", copy.deepcopy(self.participant_names)
        )
        return self


def referenced_agents(record: MeetingRecord) -> set[str]:
    """Every agent id this meeting would put in front of an operator.

    Returns:
        The ids to resolve names for.
    """
    minutes = record.minutes
    if minutes is None:
        return set()
    referenced = {minutes.leader_id, *minutes.participant_ids}
    referenced |= {c.agent_id for c in minutes.contributions}
    referenced |= {
        item.presenter_id for item in minutes.agenda.items if item.presenter_id
    }
    referenced |= {
        item.assignee_id for item in minutes.action_items if item.assignee_id
    }
    return referenced


def to_meeting_response(
    record: MeetingRecord, names: Mapping[str, str]
) -> MeetingResponse:
    """Convert a MeetingRecord to a MeetingResponse with analytics.

    Args:
        record: The domain-layer meeting record.
        names: Agent id to display name, from :func:`agent_name_map`. Resolved
            once for the whole response rather than per participant.

    Returns:
        Response DTO with per-participant token usage (sum of input +
        output tokens across all contributions), contribution ranking
        by total tokens descending, duration (when minutes are present),
        and the display name of every agent it references.
    """
    usage: dict[str, int] = {}
    rank: tuple[str, ...] = ()
    duration: float | None = None

    if record.minutes is not None:
        for c in record.minutes.contributions:
            usage[c.agent_id] = (
                usage.get(c.agent_id, 0) + c.input_tokens + c.output_tokens
            )
        rank = tuple(
            sorted(usage, key=usage.__getitem__, reverse=True),
        )
        delta = record.minutes.ended_at - record.minutes.started_at
        duration = max(0.0, delta.total_seconds())

    resolved = {
        agent_id: names[agent_id]
        for agent_id in referenced_agents(record)
        if agent_id in names
    }
    return MeetingResponse(
        **record.model_dump(),
        token_usage_by_participant=usage,
        contribution_rank=rank,
        meeting_duration_seconds=duration,
        participant_names=resolved,
    )
