# module-kind: code
"""Response models for a project's initiative progress.

The operator-facing projection of the project / plan / task graph: what the
plan asked for, which task implements each item, whether each item is done,
and which chain of items sets the delivery date.

Computed server-side and served whole, so the dashboard stays a pure API
consumer and the same view is reachable by any API client rather than existing
only in the browser.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.core.validation import set_field_names


class ProjectProgressItem(BaseModel):
    """One plan item and the live state of the task implementing it."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    item_id: UUID = Field(description="Plan item identifier")
    title: NotBlankStr = Field(description="Plan item title")
    kind: PlanItemKind = Field(description="Work or decision")
    owner: NotBlankStr | None = Field(default=None, description="Role owning the item")
    depends_on: tuple[UUID, ...] = Field(
        default=(),
        description="Plan items this one depends on",
    )
    task_id: UUID | None = Field(
        default=None,
        description="Task implementing this item",
    )
    task_status: TaskStatus | None = Field(
        default=None,
        description="Persisted status of the implementing task",
    )
    chosen_option_id: NotBlankStr | None = Field(
        default=None,
        description="Option recorded for a decision item",
    )
    done: bool = Field(
        default=False,
        description="Whether the item satisfies the completion rule",
    )
    on_critical_path: bool = Field(
        default=False,
        description="Whether the item lies on the longest dependency chain",
    )

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> ProjectProgressItem:
        """Reject an item carrying the other kind's fields.

        Returns:
            The validated model.

        Raises:
            ValueError: When the fields do not match ``kind``.
        """
        if self.kind is PlanItemKind.DECISION and (
            offending := set_field_names(
                task_id=self.task_id, task_status=self.task_status
            )
        ):
            msg = f"A DECISION item carries no task, but {offending} is set"
            raise ValueError(msg)
        if self.kind is PlanItemKind.WORK and self.chosen_option_id is not None:
            msg = "A WORK item records no chosen option, but chosen_option_id is set"
            raise ValueError(msg)
        return self


class ProjectProgressCounts(BaseModel):
    """Derived attention signal across the plan's items.

    Failed and blocked are counts rather than lifecycle states: a project never
    auto-fails, so this is how the operator sees that work needs attention.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    total: int = Field(default=0, ge=0, description="Number of plan items")
    done: int = Field(default=0, ge=0, description="Items that are done")
    failed: int = Field(default=0, ge=0, description="Items whose task failed")
    blocked: int = Field(default=0, ge=0, description="Items whose task stalled")


class ProjectProgress(BaseModel):
    """A project's initiative progress: plan, items, counts, critical path."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: UUID = Field(description="Project identifier")
    project_status: ProjectStatus = Field(description="Current project status")
    plan_id: UUID | None = Field(
        default=None,
        description="Plan the project is executing (None before dispatch)",
    )
    plan_status: PlanStatus | None = Field(
        default=None,
        description="Status of the executing plan",
    )
    objective_title: NotBlankStr | None = Field(
        default=None,
        description="What the initiative set out to do",
    )
    items: tuple[ProjectProgressItem, ...] = Field(
        default=(),
        description="Plan items with their task status, in plan order",
    )
    counts: ProjectProgressCounts = Field(
        default_factory=ProjectProgressCounts,
        description="Derived progress and attention counts",
    )
    critical_path: tuple[UUID, ...] = Field(
        default=(),
        description="Longest dependency chain through the plan, in order",
    )

    @model_validator(mode="after")
    def _validate_plan_fields(self) -> ProjectProgress:
        """Reject a partially populated plan trio.

        The three plan fields describe one fact: which plan the project is
        executing. They are all set together or all absent, so an independent
        combination is a producer bug rather than a renderable state.

        Returns:
            The validated model.

        Raises:
            ValueError: When the plan fields are partially populated.
        """
        present = {
            self.plan_id is not None,
            self.plan_status is not None,
            self.objective_title is not None,
        }
        if len(present) != 1:
            msg = "plan_id, plan_status and objective_title are all-or-nothing"
            raise ValueError(msg)
        return self
