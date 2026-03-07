"""Runtime task execution state.

Wraps the frozen ``Task`` config model with evolving execution state
(status, cost, turn count) using ``model_copy(update=...)`` for cheap,
immutable state transitions.
"""

from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ai_company.core.enums import TaskStatus
from ai_company.core.task import Task  # noqa: TC001
from ai_company.core.task_transitions import VALID_TRANSITIONS, validate_transition
from ai_company.engine.errors import ExecutionStateError
from ai_company.observability import get_logger
from ai_company.observability.events.execution import (
    EXECUTION_COST_ON_TERMINAL,
    EXECUTION_COST_RECORDED,
    EXECUTION_TASK_CREATED,
    EXECUTION_TASK_TRANSITION,
    EXECUTION_TASK_TRANSITION_FAILED,
)
from ai_company.providers.models import (
    ZERO_TOKEN_USAGE,
    TokenUsage,
    add_token_usage,
)

logger = get_logger(__name__)

_TERMINAL_STATUSES = frozenset(
    status for status, targets in VALID_TRANSITIONS.items() if not targets
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
        started_at: Set by ``with_transition`` on first entry to
            ``IN_PROGRESS`` (``None`` until then).
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
        default=ZERO_TOKEN_USAGE,
        description="Running cost totals",
    )
    turn_count: int = Field(
        default=0,
        ge=0,
        description="Number of turns completed",
    )
    retry_count: int = Field(
        default=0,
        ge=0,
        description="Number of previous failure-reassignment cycles",
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
    def from_task(
        cls,
        task: Task,
        *,
        retry_count: int = 0,
    ) -> TaskExecution:
        """Create a fresh execution from a task definition.

        Args:
            task: The frozen task to wrap.
            retry_count: Number of previous failure-reassignment cycles.

        Returns:
            New ``TaskExecution`` with status matching the task.
        """
        execution = cls(task=task, status=task.status, retry_count=retry_count)
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
        try:
            validate_transition(self.status, target)
        except ValueError:
            logger.warning(
                EXECUTION_TASK_TRANSITION_FAILED,
                task_id=self.task.id,
                from_status=self.status.value,
                to_status=target.value,
                turn_count=self.turn_count,
            )
            raise
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

        Raises:
            ExecutionStateError: If execution is in a terminal state.
        """
        if self.is_terminal:
            msg = (
                f"Cannot record cost on terminal task execution "
                f"(task_id={self.task.id}, status={self.status.value})"
            )
            logger.error(
                EXECUTION_COST_ON_TERMINAL,
                task_id=self.task.id,
                status=self.status.value,
            )
            raise ExecutionStateError(msg)
        result = self.model_copy(
            update={
                "accumulated_cost": add_token_usage(self.accumulated_cost, usage),
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
