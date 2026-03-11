"""Crash recovery strategy protocol and fail-and-reassign implementation.

Defines the ``RecoveryStrategy`` protocol and the default
``FailAndReassignStrategy`` that transitions a crashed task execution
from its current status (typically ``IN_PROGRESS``) to ``FAILED``
status, captures a redacted context snapshot, and reports whether the
task can be reassigned (based on retry count vs max retries).

See the Crash Recovery section of the Engine design page.
"""

from typing import Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, computed_field

from ai_company.core.enums import TaskStatus
from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.engine.context import AgentContext, AgentContextSnapshot  # noqa: TC001
from ai_company.engine.task_execution import TaskExecution  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.execution import (
    EXECUTION_RECOVERY_COMPLETE,
    EXECUTION_RECOVERY_SNAPSHOT,
    EXECUTION_RECOVERY_START,
)

logger = get_logger(__name__)


class RecoveryResult(BaseModel):
    """Frozen result of a recovery strategy invocation.

    Attributes:
        task_execution: Updated execution after recovery (typically
            ``FAILED`` for the default strategy).
        strategy_type: Identifier of the strategy used (e.g. ``"fail_reassign"``).
        can_reassign: Computed — ``True`` when retry_count < task.max_retries.
            The caller (task router) is responsible for incrementing
            ``retry_count`` when creating the next ``TaskExecution``.
        context_snapshot: Redacted snapshot (no message contents).
        error_message: The error that triggered recovery.
    """

    model_config = ConfigDict(frozen=True)

    task_execution: TaskExecution = Field(
        description="Updated execution with FAILED status",
    )
    strategy_type: NotBlankStr = Field(
        description="Identifier of the recovery strategy used",
    )
    context_snapshot: AgentContextSnapshot = Field(
        description="Redacted context snapshot (no message contents)",
    )
    error_message: NotBlankStr = Field(
        description="The error that triggered recovery",
    )

    @computed_field(  # type: ignore[prop-decorator]
        description="Whether the task can be reassigned for retry",
    )
    @property
    def can_reassign(self) -> bool:
        """Whether the task can be reassigned for retry.

        Assumes the caller (task router) will increment ``retry_count``
        when creating the next ``TaskExecution`` for the reassigned task.
        """
        return self.task_execution.retry_count < self.task_execution.task.max_retries


@runtime_checkable
class RecoveryStrategy(Protocol):
    """Protocol for crash recovery strategies.

    Implementations decide how to handle a failed task execution:
    transition the task, capture diagnostics, and report whether
    reassignment is possible.
    """

    async def recover(
        self,
        *,
        task_execution: TaskExecution,
        error_message: str,
        context: AgentContext,
    ) -> RecoveryResult:
        """Apply recovery to a failed task execution.

        Args:
            task_execution: Current execution state (typically
                ``IN_PROGRESS``, but may be ``ASSIGNED`` for early
                setup failures).
            error_message: Description of the failure.
            context: Full agent context at the time of failure.

        Returns:
            ``RecoveryResult`` with the updated execution and diagnostics.
        """
        ...

    def get_strategy_type(self) -> str:
        """Return the strategy type identifier."""
        ...


class FailAndReassignStrategy:
    """Default recovery: transition to FAILED and report reassignment eligibility.

    1. Capture a redacted ``AgentContextSnapshot`` (excludes message
       contents to prevent leaking sensitive prompts/tool outputs).
    2. Log the snapshot at ERROR level.
    3. Transition ``TaskExecution`` to ``FAILED`` with the error as reason.
    4. Report ``can_reassign = retry_count < task.max_retries``.
    """

    STRATEGY_TYPE: Final[str] = "fail_reassign"

    async def recover(
        self,
        *,
        task_execution: TaskExecution,
        error_message: str,
        context: AgentContext,
    ) -> RecoveryResult:
        """Apply fail-and-reassign recovery.

        Args:
            task_execution: Current execution state.
            error_message: Description of the failure.
            context: Full agent context at the time of failure.

        Returns:
            ``RecoveryResult`` with FAILED execution and reassignment info.
        """
        logger.info(
            EXECUTION_RECOVERY_START,
            task_id=task_execution.task.id,
            strategy=self.STRATEGY_TYPE,
            retry_count=task_execution.retry_count,
        )

        snapshot = context.to_snapshot()
        logger.error(
            EXECUTION_RECOVERY_SNAPSHOT,
            task_id=task_execution.task.id,
            turn_count=snapshot.turn_count,
            cost_usd=snapshot.accumulated_cost.cost_usd,
            error_message=error_message,
        )

        failed_execution = task_execution.with_transition(
            TaskStatus.FAILED,
            reason=error_message,
        )

        result = RecoveryResult(
            task_execution=failed_execution,
            strategy_type=self.STRATEGY_TYPE,
            context_snapshot=snapshot,
            error_message=error_message,
        )

        logger.info(
            EXECUTION_RECOVERY_COMPLETE,
            task_id=task_execution.task.id,
            strategy=self.STRATEGY_TYPE,
            can_reassign=result.can_reassign,
            retry_count=task_execution.retry_count,
            max_retries=task_execution.task.max_retries,
        )

        return result

    def get_strategy_type(self) -> str:
        """Return the strategy type identifier."""
        return self.STRATEGY_TYPE
