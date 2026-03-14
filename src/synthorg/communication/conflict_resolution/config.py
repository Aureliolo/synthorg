"""Conflict resolution configuration models (see Communication design page)."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.communication.enums import ConflictResolutionStrategy
from synthorg.core.types import NotBlankStr  # noqa: TC001


class DebateConfig(BaseModel):
    """Configuration for the structured debate strategy.

    Attributes:
        judge: Judge selection — ``"shared_manager"`` (lowest common
            manager), ``"ceo"`` (hierarchy root), or a named agent.
    """

    model_config = ConfigDict(frozen=True)

    judge: NotBlankStr = Field(
        default="shared_manager",
        description='Judge selection: "shared_manager", "ceo", or agent name',
    )


class HybridConfig(BaseModel):
    """Configuration for the hybrid resolution strategy.

    Attributes:
        review_agent: Agent tasked with reviewing positions.
        escalate_on_ambiguity: Whether to escalate to human
            when the review result is ambiguous.
    """

    model_config = ConfigDict(frozen=True)

    review_agent: NotBlankStr = Field(
        default="conflict_reviewer",
        description="Agent tasked with reviewing positions",
    )
    escalate_on_ambiguity: bool = Field(
        default=True,
        description="Escalate to human when ambiguous",
    )


class ConflictResolutionConfig(BaseModel):
    """Top-level conflict resolution configuration.

    Attributes:
        strategy: Default resolution strategy.
        debate: Configuration for the debate strategy.
        hybrid: Configuration for the hybrid strategy.
    """

    model_config = ConfigDict(frozen=True)

    strategy: ConflictResolutionStrategy = Field(
        default=ConflictResolutionStrategy.AUTHORITY,
        description="Default resolution strategy",
    )
    debate: DebateConfig = Field(
        default_factory=DebateConfig,
        description="Debate strategy configuration",
    )
    hybrid: HybridConfig = Field(
        default_factory=HybridConfig,
        description="Hybrid strategy configuration",
    )
