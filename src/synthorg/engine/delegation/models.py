# module-kind: code
"""Value models for blocking sub-agent delegation.

``DelegationSpec`` is the request a supervising agent's
``delegate_and_await`` tool hands to a :class:`SubAgentRunner`;
``DelegationResult`` is the bounded outcome the tool folds back into the
supervisor's conversation. Both are frozen so a delegation cannot be
mutated after the child run resolves.
"""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.currency import CurrencyCode
from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_protocol import TerminationReason

_MAX_TITLE_LENGTH: Final[int] = 256
"""Child-task title cap; matches ``CreateTaskData`` so the runner cannot
build a spec the task engine would reject."""

_MAX_DESCRIPTION_LENGTH: Final[int] = 4096
"""Child-task description cap; matches ``CreateTaskData``."""


class DelegationSpec(BaseModel):
    """A supervisor's request to run a child agent to completion inline.

    Attributes:
        target: The agent to delegate to, addressed by id or by name
            (resolved against the agent registry, id first).
        title: Short title for the child task.
        description: The sub-task the child agent must complete.
        project: Project scope the child task inherits from the
            supervisor's task, so the child runs under the same project
            budget and membership checks.
        parent_task_id: The supervising task the child is spawned under,
            recorded as the child's ``parent_task_id`` for audit.
        requested_by: The supervising agent id, recorded as the child
            task's creator.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    target: NotBlankStr = Field(description="Child agent id or name")
    title: NotBlankStr = Field(
        max_length=_MAX_TITLE_LENGTH,
        description="Short child-task title",
    )
    description: NotBlankStr = Field(
        max_length=_MAX_DESCRIPTION_LENGTH,
        description="The sub-task the child agent must complete",
    )
    project: NotBlankStr = Field(description="Project scope inherited from parent")
    parent_task_id: NotBlankStr = Field(description="Supervising task id")
    requested_by: NotBlankStr = Field(description="Supervising agent id")


class DelegationResult(BaseModel):
    """The bounded outcome of a completed child delegation.

    Attributes:
        child_task_id: The persisted child task id (for audit / resume).
        child_execution_id: The child run's execution id.
        target_agent_id: The resolved child agent id.
        termination_reason: Why the child loop stopped.
        is_success: Whether the child terminated ``COMPLETED``.
        final_answer: The child's last assistant message, or ``None``
            when the child produced no textual answer.
        transcript_summary: A bounded, human-readable digest of the
            child conversation for the supervisor to consume inline.
        total_cost: Child run cost in ``currency``.
        currency: Currency denominating ``total_cost``.
        total_turns: Number of LLM turns the child completed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    child_task_id: NotBlankStr = Field(description="Persisted child task id")
    child_execution_id: NotBlankStr = Field(description="Child run execution id")
    target_agent_id: NotBlankStr = Field(description="Resolved child agent id")
    termination_reason: TerminationReason = Field(
        description="Why the child loop stopped",
    )
    is_success: bool = Field(description="Whether the child terminated COMPLETED")
    final_answer: str | None = Field(
        default=None,
        description="The child's last assistant message, if any",
    )
    transcript_summary: str = Field(
        description="Bounded digest of the child conversation",
    )
    total_cost: float = Field(
        ge=0.0,
        description="Child run cost in the configured currency",
    )
    currency: CurrencyCode = Field(description="Currency denominating total_cost")
    total_turns: int = Field(
        ge=0,
        description="Number of LLM turns the child completed",
    )
