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
from synthorg.core.task_enums import BlockedReason, TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.core.validation import set_field_names


class ProjectProgressItem(BaseModel):
    """One plan item and the live state of the task implementing it."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    item_id: UUID = Field(description="Plan item identifier")
    title: NotBlankStr = Field(description="Plan item title")
    kind: PlanItemKind = Field(description="Work or decision")
    owner: NotBlankStr | None = Field(default=None, description="Role owning the item")
    owner_name: NotBlankStr | None = Field(
        default=None,
        description="Display name of the owner, when the owner has one",
    )
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
    blocked_reason: BlockedReason | None = Field(
        default=None,
        description="Why the implementing task is blocked, when it is",
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
                task_id=self.task_id,
                task_status=self.task_status,
                blocked_reason=self.blocked_reason,
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

    Failed and blocked are counts rather than lifecycle states. Task state
    flaps (an oracle REJECT reworks a task, a FAILED task stays reassignable),
    so it can say that work needs attention and never that the initiative is
    dead. Only a terminally-failed plan says that, and it says it through
    ``ProjectStatus.FAILED``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    total: int = Field(default=0, ge=0, description="Number of plan items")
    done: int = Field(default=0, ge=0, description="Items that are done")
    failed: int = Field(default=0, ge=0, description="Items whose task failed")
    blocked: int = Field(default=0, ge=0, description="Items whose task stalled")


class ContributorRef(BaseModel):
    """One agent who worked an initiative: the id to link by, and their name.

    Both travel because they answer different questions. The id is what a
    link to their page is built from; the name is the only half an operator
    should ever read, and is ``None`` when the roster no longer covers them.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Agent identifier")
    name: NotBlankStr | None = Field(
        default=None,
        description="Display name, when the agent is still on the roster",
    )


class ProjectProgress(BaseModel):
    """A project's initiative progress: plan, items, counts, critical path.

    ``contributors`` is derived from the tasks that ran, not stored on the
    project: an embedded roster has to be written by every actor that assigns
    a child, forever, and the field that tried it read as "nobody" in every
    deployment.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: UUID = Field(description="Project identifier")
    project_status: ProjectStatus = Field(description="Current project status")
    contributors: tuple[ContributorRef, ...] = Field(
        default=(),
        description="Agents that took work on this initiative, plus its lead",
    )
    plan_id: UUID | None = Field(
        default=None,
        description="Plan the project is executing (None before dispatch)",
    )
    plan_status: PlanStatus | None = Field(
        default=None,
        description="Status of the executing plan",
    )
    # ``NotBlankStr``, because the page renders this instead of the plan and a
    # blank string is a reason that says nothing while reading as present:
    # ``None`` is what "no reason recorded" is spelled as, and the two must
    # not both reach the surface as the same empty panel.
    plan_failure_reason: NotBlankStr | None = Field(
        default=None,
        description="Why the plan failed, when it did",
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
        """Reject a plan projection no producer could have built.

        The three identifying plan fields describe one fact: which plan the
        project is executing. They are all set together or all absent, so an
        independent combination is a producer bug rather than a renderable
        state. A failure reason belongs to a failed plan and to nothing else.

        Returns:
            The validated model.

        Raises:
            ValueError: When the plan fields are partially populated, or a
                failure reason is attached to a plan that has not failed.
        """
        present = {
            self.plan_id is not None,
            self.plan_status is not None,
            self.objective_title is not None,
        }
        if len(present) != 1:
            msg = "plan_id, plan_status and objective_title are all-or-nothing"
            raise ValueError(msg)
        # One direction only. A FAILED plan carrying no reason is refused where
        # it is written; refusing it here as well would make the page that
        # exists to explain the failure the one surface that cannot render it.
        if self.plan_failure_reason is not None and self.plan_status is not (
            PlanStatus.FAILED
        ):
            msg = (
                f"plan_failure_reason is only valid for a FAILED plan, "
                f"but plan_status is {self.plan_status}"
            )
            raise ValueError(msg)
        return self
