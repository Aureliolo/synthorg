"""Conflict resolution domain models (see Communication design page).

All models are frozen Pydantic v2 with ``NotBlankStr`` identifiers,
following the patterns established in ``delegation/models.py``.
"""

from enum import StrEnum
from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from synthorg.communication.enums import (
    ConflictResolutionStrategy,  # noqa: TC001
    ConflictType,  # noqa: TC001
)
from synthorg.core.enums import SeniorityLevel  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001

_MIN_POSITIONS = 2


class ConflictResolutionOutcome(StrEnum):
    """Outcome of a conflict resolution attempt.

    Members:
        RESOLVED_BY_AUTHORITY: Decided by seniority/hierarchy.
        RESOLVED_BY_DEBATE: Decided by structured debate + judge.
        RESOLVED_BY_HYBRID: Decided by hybrid review process.
        ESCALATED_TO_HUMAN: Escalated to human for resolution.
    """

    RESOLVED_BY_AUTHORITY = "resolved_by_authority"
    RESOLVED_BY_DEBATE = "resolved_by_debate"
    RESOLVED_BY_HYBRID = "resolved_by_hybrid"
    ESCALATED_TO_HUMAN = "escalated_to_human"


class ConflictPosition(BaseModel):
    """One agent's stance in a conflict.

    Attributes:
        agent_id: Identifier of the agent taking the position.
        agent_department: Department the agent belongs to.
        agent_level: Seniority level of the agent.
        position: Summary of the agent's stance.
        reasoning: Detailed justification for the position.
        timestamp: When the position was stated.
    """

    model_config = ConfigDict(frozen=True)

    agent_id: NotBlankStr = Field(description="Agent taking the position")
    agent_department: NotBlankStr = Field(description="Agent's department")
    agent_level: SeniorityLevel = Field(description="Agent seniority level")
    position: NotBlankStr = Field(description="Summary of the stance")
    reasoning: NotBlankStr = Field(description="Justification for the position")
    timestamp: AwareDatetime = Field(description="When position was stated")


class Conflict(BaseModel):
    """A dispute between two or more agents.

    Attributes:
        id: Unique conflict identifier (e.g. ``"conflict-a1b2c3d4e5f6"``).
        type: Category of the conflict.
        task_id: Related task, if any.
        subject: Brief description of the dispute.
        positions: Agent positions (minimum 2, unique agent IDs).
        detected_at: When the conflict was detected.
        is_cross_department: Whether agents span multiple departments
            (computed from positions).
    """

    model_config = ConfigDict(frozen=True)

    id: NotBlankStr = Field(description="Unique conflict identifier")
    type: ConflictType = Field(description="Conflict category")
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Related task ID",
    )
    subject: NotBlankStr = Field(description="Brief dispute description")
    positions: tuple[ConflictPosition, ...] = Field(
        description="Agent positions (min 2)",
    )
    detected_at: AwareDatetime = Field(description="Detection timestamp")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_cross_department(self) -> bool:
        """Whether the conflict spans multiple departments."""
        return len({p.agent_department for p in self.positions}) > 1

    @model_validator(mode="after")
    def _validate_positions(self) -> Self:
        """Require at least 2 positions with unique agent IDs."""
        if len(self.positions) < _MIN_POSITIONS:
            msg = "A conflict requires at least 2 positions"
            raise ValueError(msg)
        agent_ids = [p.agent_id for p in self.positions]
        if len(agent_ids) != len(set(agent_ids)):
            msg = "Duplicate agent_id in conflict positions"
            raise ValueError(msg)
        return self


class ConflictResolution(BaseModel):
    """Decision produced by a conflict resolution strategy.

    Attributes:
        conflict_id: ID of the resolved conflict.
        outcome: How the conflict was resolved.
        winning_agent_id: Agent whose position was chosen (None if escalated).
        winning_position: The winning position text (None if escalated).
        decided_by: Entity that made the decision (agent name or ``"human"``).
        reasoning: Explanation for the decision.
        resolved_at: When the resolution was produced.
    """

    model_config = ConfigDict(frozen=True)

    conflict_id: NotBlankStr = Field(description="Resolved conflict ID")
    outcome: ConflictResolutionOutcome = Field(description="Resolution outcome")
    winning_agent_id: NotBlankStr | None = Field(
        default=None,
        description="Winning agent (None if escalated)",
    )
    winning_position: NotBlankStr | None = Field(
        default=None,
        description="Winning position text (None if escalated)",
    )
    decided_by: NotBlankStr = Field(description="Decision maker")
    reasoning: NotBlankStr = Field(description="Decision explanation")
    resolved_at: AwareDatetime = Field(description="Resolution timestamp")

    @model_validator(mode="after")
    def _validate_outcome_consistency(self) -> Self:
        """Enforce consistency between outcome and winner fields."""
        if self.outcome == ConflictResolutionOutcome.ESCALATED_TO_HUMAN:
            if self.winning_agent_id is not None:
                msg = "winning_agent_id must be None when outcome is ESCALATED_TO_HUMAN"
                raise ValueError(msg)
            if self.winning_position is not None:
                msg = "winning_position must be None when outcome is ESCALATED_TO_HUMAN"
                raise ValueError(msg)
        else:
            if self.winning_agent_id is None:
                msg = (
                    "winning_agent_id is required when "
                    "outcome is not ESCALATED_TO_HUMAN"
                )
                raise ValueError(msg)
            if self.winning_position is None:
                msg = (
                    "winning_position is required when "
                    "outcome is not ESCALATED_TO_HUMAN"
                )
                raise ValueError(msg)
        return self


class DissentRecord(BaseModel):
    """Audit artifact for a resolved conflict.

    Preserves the losing agent's reasoning for organizational learning.

    Attributes:
        id: Unique dissent record identifier.
        conflict: The original conflict.
        resolution: The resolution decision.
        dissenting_agent_id: Agent whose position was overruled.
        dissenting_position: The overruled position text.
        strategy_used: Strategy that was used.
        timestamp: When the record was created.
        metadata: Extra key-value metadata pairs.
    """

    model_config = ConfigDict(frozen=True)

    id: NotBlankStr = Field(description="Unique dissent record ID")
    conflict: Conflict = Field(description="Original conflict")
    resolution: ConflictResolution = Field(description="Resolution decision")
    dissenting_agent_id: NotBlankStr = Field(
        description="Agent whose position was overruled",
    )
    dissenting_position: NotBlankStr = Field(
        description="Overruled position text",
    )
    strategy_used: ConflictResolutionStrategy = Field(
        description="Strategy that resolved the conflict",
    )
    timestamp: AwareDatetime = Field(description="Record creation timestamp")
    metadata: tuple[tuple[NotBlankStr, NotBlankStr], ...] = Field(
        default=(),
        description="Extra key-value metadata pairs",
    )

    @model_validator(mode="after")
    def _validate_dissent_consistency(self) -> Self:
        """Validate cross-field consistency.

        The dissenting agent must appear in the conflict's positions
        and must not be the winning agent (unless escalated to human,
        where all positions are recorded as pending human review — no
        agent is considered the winner).
        """
        agent_ids = {p.agent_id for p in self.conflict.positions}
        if self.dissenting_agent_id not in agent_ids:
            msg = (
                f"dissenting_agent_id {self.dissenting_agent_id!r} "
                f"not found in conflict positions"
            )
            raise ValueError(msg)
        if self.resolution.conflict_id != self.conflict.id:
            msg = (
                f"resolution.conflict_id {self.resolution.conflict_id!r} "
                f"does not match conflict.id {self.conflict.id!r}"
            )
            raise ValueError(msg)
        if (
            self.resolution.outcome != ConflictResolutionOutcome.ESCALATED_TO_HUMAN
            and self.dissenting_agent_id == self.resolution.winning_agent_id
        ):
            msg = (
                "dissenting_agent_id must differ from winning_agent_id "
                "for non-escalated resolutions"
            )
            raise ValueError(msg)
        return self
