# module-kind: code
"""Durable Plan entity: the reviewable, revisable breakdown of an objective.

First-class replacement for a plan that previously lived only as a transient
``DecompositionResult`` serialised into an approval's metadata. A ``Plan`` is
persisted, versioned, and revisable, so the operator can review, edit, and
converse about it before approving, and so it outlives the approval decision
(the approval merely references ``plan_id``).
"""

from collections import Counter
from datetime import datetime
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task_enums import (
    Complexity,
    CoordinationTopology,
    Stakes,
    TaskStructure,
)
from synthorg.core.types import NotBlankStr


class PlanItem(BaseModel):
    """A single unit of work within a plan: reviewable, ownable, verifiable.

    Attributes:
        id: Unique item identifier within the plan.
        title: Short item title.
        description: Detailed item description.
        dependencies: IDs of items this one depends on (the plan DAG).
        owner: Role or agent that owns this item, or ``None`` when unassigned.
        acceptance_criteria: Per-item criteria that define "done" for it.
        expected_artifacts: Deliverables this item must produce (feeds the
            fail-loud zero-artifact guard once the item runs).
        estimated_complexity: Complexity estimate for routing.
        stakes: Stakes level for stakes-aware model routing.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique item identifier within the plan")
    title: NotBlankStr = Field(description="Short item title")
    description: NotBlankStr = Field(description="Detailed item description")
    dependencies: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="IDs of items this one depends on",
    )
    owner: NotBlankStr | None = Field(
        default=None,
        description="Role or agent that owns this item",
    )
    acceptance_criteria: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Per-item criteria that define done",
    )
    expected_artifacts: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Deliverables this item must produce",
    )
    estimated_complexity: Complexity = Field(
        default=Complexity.MEDIUM,
        description="Complexity estimate for routing",
    )
    stakes: Stakes = Field(
        default=Stakes.NORMAL,
        description="Stakes level for stakes-aware model routing",
    )

    @model_validator(mode="after")
    def _validate_no_self_dependency(self) -> Self:
        """Reject an item that depends on itself.

        Returns:
            ``self`` unchanged when no self-cycle exists.

        Raises:
            ValueError: When the item's id appears in its ``dependencies``.
        """
        if self.id in self.dependencies:
            msg = f"Plan item {self.id!r} cannot depend on itself"
            raise ValueError(msg)
        return self


class Plan(BaseModel):
    """A durable, versioned, revisable plan for an objective.

    Attributes:
        id: Plan identifier (entity primary key).
        project: Project the plan belongs to.
        objective_id: Charter/objective this plan serves.
        parent_task_id: Objective task the plan decomposes.
        items: Ordered plan items forming a validated dependency DAG.
        task_structure: Classified structure of the item graph.
        coordination_topology: Selected coordination topology.
        status: Plan lifecycle status.
        forecast_id: Cost forecast released alongside the plan, if any.
        version: Revision number, bumped on each operator edit / re-plan.
        created_at: Creation timestamp (tz-aware UTC).
        updated_at: Last-revision timestamp (tz-aware UTC).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4, description="Plan identifier")
    project: NotBlankStr = Field(description="Project the plan belongs to")
    objective_id: NotBlankStr = Field(description="Charter/objective the plan serves")
    parent_task_id: NotBlankStr = Field(
        description="Objective task the plan decomposes",
    )
    items: tuple[PlanItem, ...] = Field(description="Ordered plan items")
    task_structure: TaskStructure = Field(
        default=TaskStructure.SEQUENTIAL,
        description="Classified task structure",
    )
    coordination_topology: CoordinationTopology = Field(
        default=CoordinationTopology.AUTO,
        description="Selected coordination topology",
    )
    status: PlanStatus = Field(
        default=PlanStatus.DRAFT,
        description="Plan lifecycle status",
    )
    forecast_id: UUID | None = Field(
        default=None,
        description="Cost forecast released alongside the plan",
    )
    version: int = Field(
        default=1,
        ge=1,
        description="Revision number, bumped on each edit / re-plan",
    )
    created_at: datetime = Field(description="Creation timestamp (tz-aware UTC)")
    updated_at: datetime = Field(description="Last-revision timestamp (tz-aware UTC)")

    @model_validator(mode="after")
    def _validate_items(self) -> Self:
        """Validate item-collection integrity (non-empty, unique, resolvable).

        Cycle detection across the DAG is a service-layer concern (the
        dependency-graph validator); this guards the entity's own invariants.

        Returns:
            ``self`` unchanged when items are non-empty, ids are unique, and
            every dependency points to a known item.

        Raises:
            ValueError: When ``items`` is empty, ids duplicate, or a
                dependency references an unknown item id.
        """
        if not self.items:
            msg = "a plan must contain at least one item"
            raise ValueError(msg)
        ids = [item.id for item in self.items]
        if len(ids) != len(set(ids)):
            dupes = sorted(i for i, c in Counter(ids).items() if c > 1)
            msg = f"duplicate plan item ids: {dupes}"
            raise ValueError(msg)
        id_set = set(ids)
        for item in self.items:
            missing = [d for d in item.dependencies if d not in id_set]
            if missing:
                msg = f"plan item {item.id!r} references unknown items: {missing}"
                raise ValueError(msg)
        return self
