"""Meeting protocol configuration models (DESIGN_SPEC Section 5.7)."""

from pydantic import BaseModel, ConfigDict, Field

from ai_company.communication.meeting.enums import MeetingProtocolType
from ai_company.core.types import NotBlankStr  # noqa: TC001


class RoundRobinConfig(BaseModel):
    """Configuration for the round-robin meeting protocol.

    Attributes:
        max_turns_per_agent: Maximum turns each agent may take.
        max_total_turns: Hard cap on total turns across all agents.
        leader_summarizes: Whether the leader produces a final summary.
    """

    model_config = ConfigDict(frozen=True)

    max_turns_per_agent: int = Field(
        default=2,
        ge=1,
        description="Maximum turns each agent may take",
    )
    max_total_turns: int = Field(
        default=16,
        ge=1,
        description="Hard cap on total turns across all agents",
    )
    leader_summarizes: bool = Field(
        default=True,
        description="Whether the leader produces a final summary",
    )


class PositionPapersConfig(BaseModel):
    """Configuration for the position-papers meeting protocol.

    Attributes:
        max_tokens_per_position: Token budget per position paper.
        synthesizer: Who performs synthesis.  The sentinel
            ``"meeting_leader"`` resolves to the meeting leader at
            runtime; otherwise interpreted as a specific agent ID.
    """

    model_config = ConfigDict(frozen=True)

    max_tokens_per_position: int = Field(
        default=300,
        gt=0,
        description="Token budget per position paper",
    )
    synthesizer: NotBlankStr = Field(
        default="meeting_leader",
        description="Who performs synthesis (meeting_leader or agent ID)",
    )


class StructuredPhasesConfig(BaseModel):
    """Configuration for the structured-phases meeting protocol.

    Attributes:
        skip_discussion_if_no_conflicts: Skip discussion when no
            conflicts are detected.
        max_discussion_tokens: Token budget for the discussion
            round.
    """

    model_config = ConfigDict(frozen=True)

    skip_discussion_if_no_conflicts: bool = Field(
        default=True,
        description="Skip discussion when no conflicts detected",
    )
    max_discussion_tokens: int = Field(
        default=1000,
        gt=0,
        description="Token budget for discussion round",
    )


class MeetingProtocolConfig(BaseModel):
    """Top-level meeting protocol configuration.

    Selects which protocol strategy to use and carries the
    per-protocol settings.

    Attributes:
        protocol: Which protocol strategy to use.
        auto_create_tasks: Whether to auto-create tasks from action items
            extracted during any protocol execution.
        round_robin: Round-robin protocol settings.
        position_papers: Position-papers protocol settings.
        structured_phases: Structured-phases protocol settings.
    """

    model_config = ConfigDict(frozen=True)

    protocol: MeetingProtocolType = Field(
        default=MeetingProtocolType.ROUND_ROBIN,
        description="Which protocol strategy to use",
    )
    auto_create_tasks: bool = Field(
        default=True,
        description="Auto-create tasks from action items",
    )
    round_robin: RoundRobinConfig = Field(
        default_factory=RoundRobinConfig,
        description="Round-robin protocol settings",
    )
    position_papers: PositionPapersConfig = Field(
        default_factory=PositionPapersConfig,
        description="Position-papers protocol settings",
    )
    structured_phases: StructuredPhasesConfig = Field(
        default_factory=StructuredPhasesConfig,
        description="Structured-phases protocol settings",
    )
