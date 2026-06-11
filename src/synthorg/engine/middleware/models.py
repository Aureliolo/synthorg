"""Middleware domain models.

Frozen Pydantic models for middleware context, call results,
ledger state, and assumption-violation signals.
"""

import copy
from enum import StrEnum
from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.core.agent import AgentIdentity
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.context import AgentContext
from synthorg.observability import get_logger
from synthorg.providers.models import TokenUsage

logger = get_logger(__name__)

# ── Agent middleware context ──────────────────────────────────────


class AgentMiddlewareContext(BaseModel):
    """Execution state carried through the agent middleware chain.

    Immutable-via-copy: middleware returns a new context with
    ``model_copy(update=...)`` when it needs to modify state.

    Attributes:
        agent_context: Mutable-via-copy runtime execution state.
        identity: Frozen agent configuration.
        task: Frozen task definition.
        agent_id: Agent identifier (string form of UUID).
        task_id: Task identifier.
        execution_id: Unique execution run identifier.
        effective_autonomy: Resolved autonomy for this run.
        metadata: Middleware-to-middleware data pass-through.
            Keyed by middleware name to avoid collisions.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_context: AgentContext = Field(
        description="Mutable-via-copy runtime execution state",
    )
    identity: AgentIdentity = Field(
        description="Frozen agent configuration",
    )
    task: Task = Field(description="Frozen task definition")
    agent_id: NotBlankStr = Field(
        description="Agent identifier (string UUID)",
    )
    task_id: NotBlankStr = Field(description="Task identifier")
    execution_id: NotBlankStr = Field(
        description="Unique execution run identifier",
    )
    effective_autonomy: EffectiveAutonomy | None = Field(
        default=None,
        description="Resolved autonomy for this run",
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Middleware-to-middleware data pass-through",
    )

    @model_validator(mode="after")
    def _deepcopy_metadata(self) -> AgentMiddlewareContext:
        """Defensive copy so callers cannot mutate the frozen model.

        Returns:
            ``self`` with its ``metadata`` field replaced by a deep
            copy of the input mapping.
        """
        object.__setattr__(
            self,
            "metadata",
            copy.deepcopy(self.metadata),
        )
        return self

    def with_metadata(
        self,
        key: str,
        value: object,
    ) -> AgentMiddlewareContext:
        """Return a copy with an additional metadata entry.

        Args:
            key: Metadata key (typically the middleware name).
            value: Metadata value.

        Returns:
            New context with the entry added.
        """
        updated = copy.deepcopy(self.metadata)
        updated[key] = copy.deepcopy(value)
        return self.model_copy(update={"metadata": updated})


# ── Model and tool call results ───────────────────────────────────


class ModelCallResult(BaseModel):
    """Result of a model (LLM) call passed through middleware.

    Attributes:
        response_text: The model's text response.
        token_usage: Token counts and cost for this call.
        finish_reason: Why the model stopped generating.
        error: Error description if the call failed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    response_text: str = Field(
        default="",
        description="Model text response",
    )
    token_usage: TokenUsage = Field(
        description="Token counts and cost",
    )
    finish_reason: str = Field(description="Why the model stopped")
    error: str | None = Field(
        default=None,
        description="Error description on failure",
    )


class ToolCallResult(BaseModel):
    """Result of a tool invocation passed through middleware.

    Attributes:
        tool_name: Name of the tool that was invoked.
        output: Serialized tool output.
        success: Whether the tool call succeeded.
        error: Error description if the call failed.
        metadata: Tool execution metadata (not forwarded to LLM).
        updated_agent_context: When set by middleware (e.g.
            ``DisclosureMiddleware``), carries the updated
            ``AgentContext`` back to the engine so state changes
            (loaded tools, resources, auto-unload) persist.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    tool_name: NotBlankStr = Field(
        description="Name of the invoked tool",
    )
    output: str = Field(
        default="",
        description="Serialized tool output",
    )
    success: bool = Field(
        default=True,
        description="Whether the tool call succeeded",
    )
    error: str | None = Field(
        default=None,
        description="Error description on failure",
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Tool execution metadata (not forwarded to LLM)",
    )
    updated_agent_context: AgentContext | None = Field(
        default=None,
        description="Updated agent context from middleware (disclosure state)",
    )

    @model_validator(mode="after")
    def _deepcopy_metadata(self) -> Self:
        """Defensive copy so callers cannot mutate the frozen model.

        Returns:
            ``self`` with its ``metadata`` field replaced by a deep
            copy of the input mapping.
        """
        object.__setattr__(
            self,
            "metadata",
            copy.deepcopy(self.metadata),
        )
        return self

    @model_validator(mode="after")
    def _validate_error_consistency(self) -> Self:
        """Ensure success and error fields are consistent.

        Returns:
            ``self`` unchanged when success / error fields agree.

        Raises:
            ValueError: When a successful call has an error message or
                a failed call has none.
        """
        if self.success and self.error is not None:
            msg = "successful tool call must not have an error"
            logger.warning(msg, tool_name=self.tool_name)
            raise ValueError(msg)
        if not self.success and self.error is None:
            msg = "failed tool call must have an error description"
            logger.warning(msg, tool_name=self.tool_name)
            raise ValueError(msg)
        return self


# ── Assumption violation signal ───────────────────────────────────


class AssumptionViolationType(StrEnum):
    """Classification of an assumption violation."""

    PRECONDITION_CHANGED = "precondition_changed"
    CRITERIA_CONFLICT = "criteria_conflict"
    DEPENDENCY_FAILED = "dependency_failed"


class AssumptionViolationEvent(BaseModel):
    """Signal emitted when an agent detects a broken assumption.

    Propagated as an escalation event (not a retry trigger).
    Recorded in ``AgentMiddlewareContext.metadata["assumption_violations"]``.

    Attributes:
        agent_id: Agent that detected the violation.
        task_id: Task being executed.
        violation_type: Classification of the violation.
        description: Human-readable summary.
        evidence: Supporting evidence from the model response.
        turn_number: Turn in which the violation was detected.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(
        description="Agent that detected the violation",
    )
    task_id: NotBlankStr = Field(
        description="Task being executed",
    )
    violation_type: AssumptionViolationType = Field(
        description="Classification of the violation",
    )
    description: NotBlankStr = Field(
        description="Human-readable summary",
    )
    evidence: NotBlankStr = Field(
        description="Supporting evidence from the model response",
    )
    turn_number: int = Field(
        gt=0,
        description="Turn in which the violation was detected",
    )


# ── TaskLedger and ProgressLedger ────────────────────────────────


class TaskLedger(BaseModel):
    """Frozen wrapper around a decomposition plan for coordination.

    Captures the plan text, known facts, and educated guesses at
    a point in time.  Versioned to track replan cycles.

    Attributes:
        plan_text: Serialized decomposition plan text.
        known_facts: Verified facts from the task context.
        educated_guesses: Inferred but unverified facts.
        plan_version: Monotonically increasing version counter.
        created_at: When this ledger version was created.
        superseded_at: When this version was replaced (None if current).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    plan_text: NotBlankStr = Field(
        description="Serialized decomposition plan text",
    )
    known_facts: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Verified facts from the task context",
    )
    educated_guesses: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Inferred but unverified facts",
    )
    plan_version: int = Field(
        default=1,
        ge=1,
        description="Monotonically increasing version counter",
    )
    created_at: AwareDatetime = Field(
        description="When this ledger version was created",
    )
    superseded_at: AwareDatetime | None = Field(
        default=None,
        description="When replaced by a newer version (None if current)",
    )

    @model_validator(mode="after")
    def _validate_temporal_ordering(self) -> Self:
        """Ensure superseded_at is not before created_at.

        Returns:
            ``self`` unchanged when the temporal ordering is valid.

        Raises:
            ValueError: When ``superseded_at`` predates ``created_at``.
        """
        if self.superseded_at is not None and self.superseded_at < self.created_at:
            msg = "superseded_at must be >= created_at"
            logger.warning(
                msg,
                plan_version=self.plan_version,
                created_at=str(self.created_at),
                superseded_at=str(self.superseded_at),
            )
            raise ValueError(msg)
        return self


class ProgressLedger(BaseModel):
    """Per-round coordination progress snapshot.

    Emitted by the ``after_rollup`` coordination middleware to
    track stall detection and replan decisions.

    Attributes:
        round_number: 1-indexed coordination round.
        progress_made: Whether any subtask advanced since last round.
        completed_count: Snapshot of completed subtask count this round
            (used for monotonic progress comparison across rounds).
        stall_count: Consecutive rounds with no progress.
        reset_count: Number of replan cycles executed.
        blocking_issues: Descriptions of blocking failures.
        next_action: Recommended action (continue, replan, escalate).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    round_number: int = Field(
        ge=1,
        description="1-indexed coordination round",
    )
    progress_made: bool = Field(
        description="Whether any subtask advanced",
    )
    completed_count: int = Field(
        default=0,
        ge=0,
        description="Snapshot of completed subtask count this round",
    )
    stall_count: int = Field(
        default=0,
        ge=0,
        description="Consecutive rounds with no progress",
    )
    reset_count: int = Field(
        default=0,
        ge=0,
        description="Number of replan cycles executed",
    )
    blocking_issues: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Descriptions of blocking failures",
    )
    next_action: NotBlankStr = Field(
        description="Recommended action: continue, replan, or escalate",
    )

    @model_validator(mode="after")
    def _validate_stall_progress_consistency(self) -> Self:
        """Stall count must be zero when progress was made.

        Returns:
            ``self`` unchanged when stall / progress are consistent.

        Raises:
            ValueError: When ``progress_made`` is ``True`` and
                ``stall_count > 0``.
        """
        if self.progress_made and self.stall_count > 0:
            msg = "stall_count must be 0 when progress_made is True"
            logger.warning(
                msg,
                round_number=self.round_number,
                stall_count=self.stall_count,
            )
            raise ValueError(msg)
        return self
