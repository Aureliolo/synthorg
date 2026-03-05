"""Runtime task execution state.

Wraps the frozen ``Task`` config model with evolving execution state
(status, cost, turn count) using ``model_copy(update=...)`` for cheap,
immutable state transitions.
"""

from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ai_company.core.enums import TaskStatus
from ai_company.core.task import Task  # noqa: TC001
from ai_company.core.task_transitions import validate_transition
from ai_company.observability import get_logger
from ai_company.observability.events import (
    EXECUTION_COST_RECORDED,
    EXECUTION_TASK_CREATED,
    EXECUTION_TASK_TRANSITION,
)
from ai_company.providers.models import TokenUsage

logger = get_logger(__name__)

_ZERO_USAGE = TokenUsage(
    input_tokens=0,
    output_tokens=0,
    total_tokens=0,
    cost_usd=0.0,
)

_TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.CANCELLED})


def _add_token_usage(a: TokenUsage, b: TokenUsage) -> TokenUsage:
    """Create a new ``TokenUsage`` with summed fields.

    Computes ``total_tokens`` from the summed parts to maintain the
    ``total_tokens == input_tokens + output_tokens`` invariant.

    Args:
        a: First usage record.
        b: Second usage record.

    Returns:
        New ``TokenUsage`` with all fields summed.
    """
    input_tokens = a.input_tokens + b.input_tokens
    output_tokens = a.output_tokens + b.output_tokens
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cost_usd=a.cost_usd + b.cost_usd,
    )


class StatusTransition(BaseModel):
    """Frozen audit record for a single status transition.

    Attributes:
        from_status: Status before the transition.
        to_status: Status after the transition.
        timestamp: When the transition occurred (timezone-aware).
        reason: Optional human-readable reason for the transition.
    """

    model_config = ConfigDict(frozen=True)

    from_status: TaskStatus = Field(description="Status before transition")
    to_status: TaskStatus = Field(description="Status after transition")
    timestamp: AwareDatetime = Field(
        description="When the transition occurred",
    )
    reason: str = Field(
        default="",
        description="Optional reason for the transition",
    )


class TaskExecution(BaseModel):
    """Frozen runtime wrapper around a ``Task`` for execution tracking.

    All state evolution happens via ``model_copy(update=...)``.
    Transitions are validated explicitly via
    :func:`~ai_company.core.task_transitions.validate_transition` before
    the copy is made.

    Attributes:
        task: Original frozen task definition.
        status: Current execution status (starts from ``task.status``).
        transition_log: Audit trail of status transitions.
        accumulated_cost: Running token usage and cost totals.
        turn_count: Number of LLM turns completed.
        started_at: When execution first entered ``IN_PROGRESS``.
        completed_at: When execution reached a terminal state.
    """

    model_config = ConfigDict(frozen=True)

    task: Task = Field(description="Original frozen task definition")
    status: TaskStatus = Field(description="Current execution status")
    transition_log: tuple[StatusTransition, ...] = Field(
        default=(),
        description="Audit trail of status transitions",
    )
    accumulated_cost: TokenUsage = Field(
        default=_ZERO_USAGE,
        description="Running cost totals",
    )
    turn_count: int = Field(
        default=0,
        ge=0,
        description="Number of turns completed",
    )
    started_at: AwareDatetime | None = Field(
        default=None,
        description="When execution entered IN_PROGRESS",
    )
    completed_at: AwareDatetime | None = Field(
        default=None,
        description="When execution reached a terminal state",
    )

    @classmethod
    def from_task(cls, task: Task) -> TaskExecution:
        """Create a fresh execution from a task definition.

        Args:
            task: The frozen task to wrap.

        Returns:
            New ``TaskExecution`` with status matching the task.
        """
        execution = cls(task=task, status=task.status)
        logger.debug(
            EXECUTION_TASK_CREATED,
            task_id=task.id,
            initial_status=task.status.value,
        )
        return execution

    def with_transition(
        self,
        target: TaskStatus,
        *,
        reason: str = "",
    ) -> TaskExecution:
        """Validate and apply a status transition.

        Calls :func:`~ai_company.core.task_transitions.validate_transition`
        then returns a new ``TaskExecution`` via ``model_copy``.  Sets
        ``started_at`` on first entry to ``IN_PROGRESS`` and
        ``completed_at`` on terminal states.

        Args:
            target: The desired target status.
            reason: Optional reason for the transition.

        Returns:
            New ``TaskExecution`` with updated status and transition log.

        Raises:
            ValueError: If the transition is invalid.
        """
        validate_transition(self.status, target)
        now = datetime.now(UTC)
        transition = StatusTransition(
            from_status=self.status,
            to_status=target,
            timestamp=now,
            reason=reason,
        )
        updates: dict[str, object] = {
            "status": target,
            "transition_log": (*self.transition_log, transition),
        }
        if target is TaskStatus.IN_PROGRESS and self.started_at is None:
            updates["started_at"] = now
        if target in _TERMINAL_STATUSES:
            updates["completed_at"] = now

        result = self.model_copy(update=updates)
        logger.info(
            EXECUTION_TASK_TRANSITION,
            task_id=self.task.id,
            from_status=self.status.value,
            to_status=target.value,
            reason=reason,
        )
        return result

    def with_cost(self, usage: TokenUsage) -> TaskExecution:
        """Accumulate token usage and increment turn count.

        Args:
            usage: Token usage from a single LLM call.

        Returns:
            New ``TaskExecution`` with updated cost and turn count.
        """
        result = self.model_copy(
            update={
                "accumulated_cost": _add_token_usage(self.accumulated_cost, usage),
                "turn_count": self.turn_count + 1,
            }
        )
        logger.debug(
            EXECUTION_COST_RECORDED,
            task_id=self.task.id,
            turn=result.turn_count,
            cost_usd=usage.cost_usd,
        )
        return result

    def to_task_snapshot(self) -> Task:
        """Return the original task with the current execution status.

        Useful for persistence or reporting where a plain ``Task`` is
        expected.

        Returns:
            A copy of the original task with updated status.
        """
        return self.task.model_copy(update={"status": self.status})

    @property
    def is_terminal(self) -> bool:
        """Whether execution is in a terminal state."""
        return self.status in _TERMINAL_STATUSES
