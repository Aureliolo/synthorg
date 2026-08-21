# module-kind: declarative
"""What a parent task's subtasks add up to.

Kept apart from :mod:`synthorg.engine.decomposition.models`, which describes
what a decomposition IS: its inputs, its plan and its tree. This describes what
its execution has come to, which is a different question asked by a different
half of the system (the coordination rollup, the middleware relay and the
parent-status writer).
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr


class SubtaskStatusRollup(BaseModel):
    """Aggregated status of subtasks for a parent task.

    Tracks six explicit statuses: COMPLETED, FAILED, IN_PROGRESS,
    BLOCKED, CANCELLED, and SUSPENDED. Other statuses (CREATED,
    ASSIGNED, IN_REVIEW, INTERRUPTED) are not individually tracked;
    the gap between the sum of tracked counts and ``total`` accounts
    for these. The ``derived_parent_status`` treats any such remainder
    as work still pending (IN_PROGRESS).

    When all subtasks are in terminal states but with a mix of
    completed and cancelled, ``derived_parent_status`` returns
    ``CANCELLED`` (some work was abandoned).

    Attributes:
        parent_task_id: ID of the parent task.
        total: Total number of subtasks.
        completed: Count of COMPLETED subtasks.
        failed: Count of FAILED subtasks.
        in_progress: Count of IN_PROGRESS subtasks.
        blocked: Count of BLOCKED subtasks.
        cancelled: Count of CANCELLED subtasks.
        suspended: Count of SUSPENDED subtasks.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    parent_task_id: NotBlankStr = Field(description="Parent task ID")
    total: int = Field(ge=0, description="Total subtasks")
    completed: int = Field(ge=0, description="Completed subtasks")
    failed: int = Field(ge=0, description="Failed subtasks")
    in_progress: int = Field(ge=0, description="In-progress subtasks")
    blocked: int = Field(ge=0, description="Blocked subtasks")
    cancelled: int = Field(ge=0, description="Cancelled subtasks")
    suspended: int = Field(ge=0, default=0, description="Suspended subtasks")

    @model_validator(mode="after")
    def _validate_counts(self) -> Self:
        """Ensure counts don't exceed total.

        Returns:
            ``self`` unchanged when status counts sum to <= ``total``.

        Raises:
            ValueError: When the sum of per-status counts exceeds
                ``total``.
        """
        counted = (
            self.completed
            + self.failed
            + self.in_progress
            + self.blocked
            + self.cancelled
            + self.suspended
        )
        if counted > self.total:
            msg = "Sum of status counts exceeds total"
            raise ValueError(msg)
        return self

    @computed_field(
        description="Derived parent task status from subtask statuses",
    )
    @property
    def derived_parent_status(self) -> TaskStatus:
        """Derive the parent task status from subtask statuses."""
        if self.total == 0:
            return TaskStatus.CREATED

        if self.completed == self.total:
            return TaskStatus.COMPLETED

        if self.cancelled == self.total:
            return TaskStatus.CANCELLED

        if self.failed > 0:
            return TaskStatus.FAILED

        if self.in_progress > 0:
            return TaskStatus.IN_PROGRESS

        if self.blocked > 0:
            return TaskStatus.BLOCKED

        if self.suspended > 0:
            return TaskStatus.SUSPENDED

        # All subtasks in terminal states but mixed completed + cancelled
        # -- not fully completed (pure completed already handled above),
        # and not fully cancelled (pure cancelled already handled above).
        # Report as CANCELLED since some work was abandoned.
        if self.completed + self.cancelled == self.total:
            return TaskStatus.CANCELLED

        return TaskStatus.IN_PROGRESS


__all__ = ["SubtaskStatusRollup"]
