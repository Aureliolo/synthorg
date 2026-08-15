# module-kind: declarative
"""Completion-oracle peer-review gate input value object.

``CompletionOracleReviewInput`` is what the peer-review gate sees on
entry: a deliverable plus the context an independent reviewer needs to
judge it. It carries the executor's agent id so the gate can enforce the
reviewer-is-distinct invariant, and the acceptance criteria the reviewer
verifies the deliverable against.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.task_enums import Complexity, Stakes
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
        stakes: How consequential the reviewed work is. Together with
            ``estimated_complexity`` this decides the capability the review
            demands, and therefore WHICH role holder is asked to perform it.
            Required, with no default: a caller that omitted them would get a
            mid-tier judge for work the org classified as critical, and
            nothing downstream could tell that apart from a deliberate one.
        estimated_complexity: The reviewed work's complexity, the second
            half of that requirement, required for the same reason.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_id: NotBlankStr
    execution_id: NotBlankStr
    deliverable_content: NotBlankStr
    acceptance_criteria: tuple[NotBlankStr, ...] = Field(min_length=1)
    executor_agent_id: NotBlankStr
    stakes: Stakes
    estimated_complexity: Complexity
    project_id: NotBlankStr | None = None
