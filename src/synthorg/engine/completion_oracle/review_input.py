# module-kind: declarative
"""Completion-oracle peer-review gate input value object.

``CompletionOracleReviewInput`` is what the peer-review gate sees on
entry: a deliverable plus the context an independent reviewer needs to
judge it. It carries the executor's agent id so the gate can enforce the
reviewer-is-distinct invariant, and the acceptance criteria the reviewer
verifies the deliverable against.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class CompletionOracleReviewInput(BaseModel):
    """What the peer-review gate sees on entry: deliverable plus context.

    Attributes:
        task_id: The deliverable's owning task.
        execution_id: The execution that produced the deliverable.
        deliverable_content: The artifact text the reviewer inspects.
        acceptance_criteria: The brief's acceptance criteria the reviewer
            verifies the deliverable against.
        executor_agent_id: The agent that produced the deliverable
            (forbidden as its own reviewer; enforced by the gate and by
            :class:`CompletionOracleReport`).
        project_id: Owning project of the deliverable, when known.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_id: NotBlankStr
    execution_id: NotBlankStr
    deliverable_content: NotBlankStr
    acceptance_criteria: tuple[NotBlankStr, ...] = Field(min_length=1)
    executor_agent_id: NotBlankStr
    project_id: NotBlankStr | None = None
