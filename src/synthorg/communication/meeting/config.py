"""Meeting protocol configuration models (see Communication design page)."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.communication.meeting.enums import (
    ConflictDetectorType,
    MeetingProtocolType,
)
from synthorg.core.types import NotBlankStr


class RoundRobinConfig(BaseModel):
    """Configuration for the round-robin meeting protocol.

    Attributes:
        max_turns_per_agent: Maximum turns each agent may take.
        max_total_turns: Hard cap on total turns across all agents.
        leader_summarizes: Whether the leader produces a final summary.
        summary_reserve_fraction: Fraction of the token budget reserved
            for the summary phase (0.0--1.0).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    summary_reserve_fraction: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Fraction of token budget reserved for summary phase",
    )


class PositionPapersConfig(BaseModel):
    """Configuration for the position-papers meeting protocol.

    Attributes:
        max_tokens_per_position: Token budget per position paper.
        synthesizer: Who performs synthesis.  The sentinel
            ``"meeting_leader"`` resolves to the meeting leader at
            runtime; otherwise interpreted as a specific agent ID.
        synthesis_reserve_fraction: Fraction of the token budget reserved
            for the synthesis phase (0.0--1.0).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_tokens_per_position: int = Field(
        default=300,
        gt=0,
        description="Token budget per position paper",
    )
    synthesizer: NotBlankStr = Field(
        default="meeting_leader",
        description="Who performs synthesis (meeting_leader or agent ID)",
    )
    synthesis_reserve_fraction: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Fraction of token budget reserved for synthesis phase",
    )


class StructuredPhasesConfig(BaseModel):
    """Configuration for the structured-phases meeting protocol.

    Attributes:
        skip_discussion_if_no_conflicts: Skip discussion when no
            conflicts are detected.
        max_discussion_tokens: Token budget for the discussion
            round.
        synthesis_reserve_fraction: Fraction of the remaining token
            budget reserved for the synthesis phase (0.0--1.0).
        conflict_detector: Which conflict-detection strategy the
            structured-phases protocol uses to decide whether
            discussion is needed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    skip_discussion_if_no_conflicts: bool = Field(
        default=True,
        description="Skip discussion when no conflicts detected",
    )
    max_discussion_tokens: int = Field(
        default=1000,
        gt=0,
        description="Token budget for discussion round",
    )
    synthesis_reserve_fraction: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Fraction of remaining token budget reserved for synthesis",
    )
    conflict_detector: ConflictDetectorType = Field(
        default=ConflictDetectorType.KEYWORD,
        description="Conflict-detection strategy discriminator",
    )


class MeetingProtocolConfig(BaseModel):
    """Top-level meeting protocol configuration.

    Selects which protocol strategy to use and carries the
    per-protocol settings. The three sub-config fields below are all
    materialised eagerly with sensible defaults; the factory consumes
    ONLY the sub-config matching :attr:`protocol`. Setting fields on a
    sub-config that does not match the active protocol (for example
    ``position_papers.synthesizer = "alice"`` while ``protocol`` is
    ``ROUND_ROBIN``) is silently ignored. Pydantic discriminated
    unions would express this invariant in the type system but were
    deferred so the YAML config can serialise every sub-config slot
    independently without a discriminator wrapper.

    Attributes:
        protocol: Which protocol strategy to use.
        auto_create_tasks: Whether to auto-create tasks from action items
            extracted during any protocol execution.
        max_tasks_per_meeting: Optional cap on how many tasks to create
            from a single meeting's action items.
        round_robin: Round-robin protocol settings (used only when
            ``protocol == ROUND_ROBIN``).
        position_papers: Position-papers protocol settings (used only
            when ``protocol == POSITION_PAPERS``).
        structured_phases: Structured-phases protocol settings (used
            only when ``protocol == STRUCTURED_PHASES``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    protocol: MeetingProtocolType = Field(
        default=MeetingProtocolType.ROUND_ROBIN,
        description="Which protocol strategy to use",
    )
    auto_create_tasks: bool = Field(
        default=True,
        description="Auto-create tasks from action items",
    )
    max_tasks_per_meeting: int | None = Field(
        default=None,
        ge=1,
        description="Maximum tasks to create from a single meeting's action items",
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
