"""Trust domain models.

Frozen Pydantic models for trust state, change records, and
evaluation results used by the progressive trust system.
"""

from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
)

from synthorg.core.enums import ToolAccessLevel
from synthorg.core.types import NotBlankStr
from synthorg.security.trust.enums import TrustChangeReason  # noqa: TC001


class TrustState(BaseModel):
    """Current trust state for an agent.

    Attributes:
        agent_id: The agent this trust state belongs to.
        global_level: Current global trust/access level.
        created_at: When trust tracking was initialized for this agent.
        category_levels: Per-category trust levels (per_category strategy).
        trust_score: Weighted trust score (weighted strategy).
        last_evaluated_at: When trust was last evaluated.
        last_promoted_at: When trust level was last promoted.
        last_decay_check_at: When decay was last checked.
        milestone_progress: Milestone tracking data (milestone strategy).
    """

    model_config = ConfigDict(frozen=True)

    agent_id: NotBlankStr = Field(description="Agent identifier")
    global_level: ToolAccessLevel = Field(
        default=ToolAccessLevel.SANDBOXED,
        description="Current global trust level",
    )
    created_at: AwareDatetime | None = Field(
        default=None,
        description="When trust tracking was initialized for this agent",
    )
    category_levels: dict[str, ToolAccessLevel] = Field(
        default_factory=dict,
        description="Per-category trust levels",
    )
    trust_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Weighted trust score",
    )
    last_evaluated_at: AwareDatetime | None = Field(
        default=None,
        description="When trust was last evaluated",
    )
    last_promoted_at: AwareDatetime | None = Field(
        default=None,
        description="When trust level was last promoted",
    )
    last_decay_check_at: AwareDatetime | None = Field(
        default=None,
        description="When decay was last checked",
    )
    milestone_progress: dict[str, int | float] = Field(
        default_factory=dict,
        description="Milestone tracking data",
    )


class TrustChangeRecord(BaseModel):
    """Record of a trust level change for audit purposes.

    Attributes:
        id: Unique record identifier.
        agent_id: Agent whose trust changed.
        old_level: Previous trust level.
        new_level: New trust level.
        category: Tool category (None for global changes).
        reason: Reason for the change.
        timestamp: When the change occurred.
        approval_id: Approval item ID if human-approved.
        details: Human-readable details.
    """

    model_config = ConfigDict(frozen=True)

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Unique record identifier",
    )
    agent_id: NotBlankStr = Field(description="Agent whose trust changed")
    old_level: ToolAccessLevel = Field(description="Previous trust level")
    new_level: ToolAccessLevel = Field(description="New trust level")
    category: NotBlankStr | None = Field(
        default=None,
        description="Tool category (None for global changes)",
    )
    reason: TrustChangeReason = Field(description="Reason for the change")
    timestamp: AwareDatetime = Field(description="When the change occurred")
    approval_id: NotBlankStr | None = Field(
        default=None,
        description="Approval item ID if human-approved",
    )
    details: str = Field(default="", description="Human-readable details")


class TrustEvaluationResult(BaseModel):
    """Result of a trust evaluation by a strategy.

    Attributes:
        agent_id: Agent evaluated.
        recommended_level: Recommended trust level.
        current_level: Current trust level.
        requires_human_approval: Whether human approval is needed.
        score: Trust score (strategy-dependent).
        details: Human-readable explanation.
        strategy_name: Name of the strategy that produced this result.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_id: NotBlankStr = Field(description="Agent evaluated")
    recommended_level: ToolAccessLevel = Field(
        description="Recommended trust level",
    )
    current_level: ToolAccessLevel = Field(description="Current trust level")
    requires_human_approval: bool = Field(
        default=False,
        description="Whether human approval is needed",
    )
    score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Trust score",
    )
    details: str = Field(default="", description="Human-readable explanation")
    strategy_name: NotBlankStr = Field(
        description="Strategy that produced this result",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def should_change(self) -> bool:
        """Whether the trust level should change."""
        return self.recommended_level != self.current_level
