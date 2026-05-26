"""Scaling domain models.

Frozen Pydantic models for scaling signals, context, decisions,
and action records.
"""

from collections.abc import Mapping  # noqa: TC003
from typing import Any, Self
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.enums import (
    ScalingActionType,
    ScalingOutcome,
    ScalingStrategyName,
)
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_SCALING_MODEL_VALIDATION_FAILED

logger = get_logger(__name__)


class ScalingSignal(BaseModel):
    """A named signal value collected from a subsystem.

    Attributes:
        name: Signal identifier (e.g. ``avg_utilization``).
        value: Current signal value.
        threshold: Configured threshold for this signal.
        source: Which signal source produced this.
        timestamp: When the signal was collected.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Signal identifier")
    value: float = Field(description="Current signal value")
    threshold: float | None = Field(
        default=None,
        description="Configured threshold for this signal",
    )
    source: NotBlankStr = Field(description="Signal source name")
    timestamp: AwareDatetime = Field(description="Collection timestamp")


class ScalingContext(BaseModel):
    """Aggregated snapshot of company state for strategy evaluation.

    Attributes:
        active_agent_count: Number of currently active agents.
        agent_ids: IDs of all active agents.
        workload_signals: Workload-related signals.
        budget_signals: Budget-related signals.
        performance_signals: Performance-related signals.
        skill_signals: Skill coverage signals.
        evaluated_at: When the context was built.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="IDs of all active agents",
    )
    workload_signals: tuple[ScalingSignal, ...] = Field(
        default=(),
        description="Workload-related signals",
    )
    budget_signals: tuple[ScalingSignal, ...] = Field(
        default=(),
        description="Budget-related signals",
    )
    performance_signals: tuple[ScalingSignal, ...] = Field(
        default=(),
        description="Performance-related signals",
    )
    skill_signals: tuple[ScalingSignal, ...] = Field(
        default=(),
        description="Skill coverage signals",
    )
    performance_snapshots: Mapping[NotBlankStr, Any] = Field(
        default_factory=dict,
        description="Raw performance snapshots keyed by agent_id",
    )
    evaluated_at: AwareDatetime = Field(
        description="When the context was built",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active_agent_count(self) -> int:
        """Derived from agent_ids length."""
        return len(self.agent_ids)


class ScalingDecision(BaseModel):
    """A scaling action proposed by a strategy.

    Attributes:
        id: Unique decision identifier.
        action_type: Type of scaling action.
        source_strategy: Which strategy proposed this.
        target_agent_id: Agent targeted for pruning (None for hires).
        target_role: Role to hire for (None for prunes).
        target_skills: Skills required for hire target.
        target_department: Department for hire target.
        rationale: Human-readable explanation.
        confidence: Strategy confidence in this decision (0.0--1.0).
        signals: Signals that informed this decision.
        created_at: When the decision was created.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Unique decision identifier",
    )
    action_type: ScalingActionType = Field(
        description="Type of scaling action",
    )
    source_strategy: ScalingStrategyName = Field(
        description="Which strategy proposed this",
    )
    target_agent_id: NotBlankStr | None = Field(
        default=None,
        description="Agent targeted for pruning",
    )
    target_role: NotBlankStr | None = Field(
        default=None,
        description="Role to hire for",
    )
    target_skills: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Skills required for hire target",
    )
    target_department: NotBlankStr | None = Field(
        default=None,
        description="Department for hire target",
    )
    rationale: NotBlankStr = Field(
        description="Human-readable explanation",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Strategy confidence (0.0--1.0)",
    )
    signals: tuple[ScalingSignal, ...] = Field(
        default=(),
        description="Signals that informed this decision",
    )
    created_at: AwareDatetime = Field(
        description="When the decision was created",
    )

    @model_validator(mode="after")
    def _validate_target_fields(self) -> Self:
        """Validate target fields match the action type.

        HIRE decisions must have a target_role.
        PRUNE decisions must have a target_agent_id.
        HOLD and NO_OP require neither.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.action_type == ScalingActionType.HIRE and self.target_role is None:
            msg = "HIRE decisions must specify target_role"
            logger.warning(
                HR_SCALING_MODEL_VALIDATION_FAILED,
                model="ScalingDecision",
                field="target_fields",
                action_type="HIRE",
                reason="missing_target_role",
            )
            raise ValueError(msg)
        if self.action_type == ScalingActionType.PRUNE and self.target_agent_id is None:
            msg = "PRUNE decisions must specify target_agent_id"
            logger.warning(
                HR_SCALING_MODEL_VALIDATION_FAILED,
                model="ScalingDecision",
                field="target_fields",
                action_type="PRUNE",
                reason="missing_target_agent_id",
            )
            raise ValueError(msg)
        return self


class ScalingActionRecord(BaseModel):
    """Record of an executed (or attempted) scaling decision.

    Attributes:
        id: Unique record identifier.
        decision_id: The decision that was executed.
        outcome: Execution outcome.
        result_id: ID of the created entity (hire request ID,
            offboarding record ID, or approval item ID).
        reason: Additional context (e.g. failure message).
        executed_at: When execution occurred.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Unique record identifier",
    )
    decision_id: NotBlankStr = Field(
        description="Decision that was executed",
    )
    outcome: ScalingOutcome = Field(description="Execution outcome")
    result_id: NotBlankStr | None = Field(
        default=None,
        description="ID of created entity",
    )
    reason: NotBlankStr | None = Field(
        default=None,
        description="Additional context",
    )
    executed_at: AwareDatetime = Field(description="When execution occurred")

    @model_validator(mode="after")
    def _validate_outcome_fields(self) -> Self:
        """Validate outcome-dependent field presence.

        - FAILED outcomes must include a reason.
        - EXECUTED/DEFERRED outcomes should carry a result_id.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.outcome == ScalingOutcome.FAILED and self.reason is None:
            msg = "FAILED records must include reason"
            logger.warning(
                HR_SCALING_MODEL_VALIDATION_FAILED,
                model="ScalingActionRecord",
                field="outcome_fields",
                outcome="FAILED",
                reason="missing_reason",
            )
            raise ValueError(msg)
        if self.outcome == ScalingOutcome.REJECTED and self.reason is None:
            msg = "REJECTED records must include reason"
            logger.warning(
                HR_SCALING_MODEL_VALIDATION_FAILED,
                model="ScalingActionRecord",
                field="outcome_fields",
                outcome="REJECTED",
                reason="missing_reason",
            )
            raise ValueError(msg)
        if (
            self.outcome in (ScalingOutcome.EXECUTED, ScalingOutcome.DEFERRED)
            and self.result_id is None
        ):
            msg = f"{self.outcome.value} records must include result_id"
            logger.warning(
                HR_SCALING_MODEL_VALIDATION_FAILED,
                model="ScalingActionRecord",
                field="outcome_fields",
                outcome=self.outcome.value,
                reason="missing_result_id",
            )
            raise ValueError(msg)
        return self
