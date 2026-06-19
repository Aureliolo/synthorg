"""Conflict resolution domain models (see Communication design page).

All models are frozen Pydantic v2 with ``NotBlankStr`` identifiers,
following the patterns established in ``delegation/models.py``.
"""

from enum import StrEnum
from typing import Final, Self
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from synthorg.communication.enums import (
    ConflictResolutionStrategy,
    ConflictType,
)
from synthorg.core.types import NotBlankStr
from synthorg.hr.seniority import SeniorityLevel

_MIN_POSITIONS: Final[int] = 2


class ConflictResolutionOutcome(StrEnum):
    """Outcome of a conflict resolution attempt.

    Members:
        RESOLVED_BY_AUTHORITY: Decided by seniority/hierarchy.
        RESOLVED_BY_DEBATE: Decided by structured debate + judge.
        RESOLVED_BY_HYBRID: Decided by hybrid review process.
        RESOLVED_BY_HUMAN: Human operator picked a winning position via the
            escalation queue.
        REJECTED_BY_HUMAN: Human operator rejected all positions via the
            escalation queue.
        ESCALATED_TO_HUMAN: Escalation pending or timed out with no human
            decision collected.
    """

    RESOLVED_BY_AUTHORITY = "resolved_by_authority"
    RESOLVED_BY_DEBATE = "resolved_by_debate"
    RESOLVED_BY_HYBRID = "resolved_by_hybrid"
    RESOLVED_BY_HUMAN = "resolved_by_human"
    REJECTED_BY_HUMAN = "rejected_by_human"
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: UUID = Field(default_factory=uuid4, description="Unique conflict identifier")
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

    @computed_field
    @property
    def is_cross_department(self) -> bool:
        """Whether the conflict spans multiple departments."""
        return len({p.agent_department for p in self.positions}) > 1

    @model_validator(mode="after")
    def _validate_positions(self) -> Self:
        """Require at least 2 positions with unique agent IDs.

        Returns:
            The validated conflict.

        Raises:
            ValueError: If there are fewer than two positions or agent
                IDs are not unique.
        """
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
        """Enforce consistency between outcome and winner fields.

        ``ESCALATED_TO_HUMAN`` and ``REJECTED_BY_HUMAN`` are no-winner
        outcomes: both winning fields must be ``None``.  Every other
        outcome must carry both a winning agent and a winning position.

        Returns:
            The validated resolution.

        Raises:
            ValueError: If the winner fields are inconsistent with the
                outcome.
        """
        no_winner_outcomes = {
            ConflictResolutionOutcome.ESCALATED_TO_HUMAN,
            ConflictResolutionOutcome.REJECTED_BY_HUMAN,
        }
        if self.outcome in no_winner_outcomes:
            if self.winning_agent_id is not None:
                msg = f"winning_agent_id must be None when outcome is {self.outcome}"
                raise ValueError(msg)
            if self.winning_position is not None:
                msg = f"winning_position must be None when outcome is {self.outcome}"
                raise ValueError(msg)
        else:
            if self.winning_agent_id is None:
                msg = f"winning_agent_id is required when outcome is {self.outcome}"
                raise ValueError(msg)
            if self.winning_position is None:
                msg = f"winning_position is required when outcome is {self.outcome}"
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4, description="Unique dissent record ID")
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
        where all positions are recorded as pending human review -- no
        agent is considered the winner).

        Returns:
            The validated dissent record.

        Raises:
            ValueError: If the dissenting agent is absent from the
                conflict or is the winning agent.
        """
        agent_ids = {p.agent_id for p in self.conflict.positions}
        if self.dissenting_agent_id not in agent_ids:
            msg = (
                f"dissenting_agent_id {self.dissenting_agent_id!r} "
                f"not found in conflict positions"
            )
            raise ValueError(msg)
        if self.resolution.conflict_id != str(self.conflict.id):
            msg = (
                f"resolution.conflict_id {self.resolution.conflict_id!r} "
                f"does not match conflict.id {str(self.conflict.id)!r}"
            )
            raise ValueError(msg)
        no_winner_outcomes = {
            ConflictResolutionOutcome.ESCALATED_TO_HUMAN,
            ConflictResolutionOutcome.REJECTED_BY_HUMAN,
        }
        if (
            self.resolution.outcome not in no_winner_outcomes
            and self.dissenting_agent_id == self.resolution.winning_agent_id
        ):
            msg = (
                "dissenting_agent_id must differ from winning_agent_id "
                "for outcomes that have a winner"
            )
            raise ValueError(msg)
        return self


class DissentPayload(BaseModel):
    """Typed payload for DISSENT bus messages.

    Extracts the key fields from a ``DissentRecord`` into a
    structured, serializable payload suitable for the internal
    message bus and SSE event stream.

    Attributes:
        dissent_id: Unique dissent record identifier.
        conflict_id: Identifier of the originating conflict.
        dissenting_agent_id: Agent whose position was overruled.
        conflict_type: Type of the originating conflict.
        strategy_used: Resolution strategy that was applied.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    dissent_id: NotBlankStr = Field(description="Dissent record ID")
    conflict_id: NotBlankStr = Field(description="Originating conflict ID")
    dissenting_agent_id: NotBlankStr = Field(
        description="Agent whose position was overruled",
    )
    conflict_type: ConflictType = Field(description="Conflict type")
    strategy_used: ConflictResolutionStrategy = Field(
        description="Resolution strategy",
    )

    @classmethod
    def from_record(cls, record: DissentRecord) -> DissentPayload:
        """Build a payload from a dissent record.

        Args:
            record: The dissent record.

        Returns:
            A typed dissent payload.
        """
        return cls(
            dissent_id=str(record.id),
            conflict_id=str(record.conflict.id),
            dissenting_agent_id=record.dissenting_agent_id,
            conflict_type=record.conflict.type,
            strategy_used=record.strategy_used,
        )
