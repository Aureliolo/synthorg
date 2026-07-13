# module-kind: code
"""Durable Plan entity: the reviewable, revisable breakdown of an objective.

A ``Plan`` is the first-class, persisted, versioned form of an objective's
decomposition: the operator can review and edit it before approving, and it
outlives the approval decision (the approval references only ``plan_id``). It
is distinct from the transient ``DecompositionResult`` the engine dispatches;
the two are projected onto each other by ``engine.decomposition.plan_mapping``.
"""

from collections import Counter
from typing import Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

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
        required_skills: Skill IDs the routing scorer matches against.
        required_tags: Tags for multi-faceted routing match.
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
        min_length=1,
        description="Per-item criteria that define done (never empty)",
    )
    expected_artifacts: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Deliverables this item must produce",
    )
    required_skills: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Skill IDs the routing scorer matches against",
    )
    required_tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Tags for multi-faceted routing match",
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
    def _validate_item(self) -> Self:
        """Reject an id that is not a canonical UUID, or a bad dependency list.

        The id must be a canonical UUID string: the dispatch path rebuilds each
        child task via ``subtask_uuid(item.id)``, which rejects a non-UUID id,
        so an operator edit adding ``"my-item"`` would otherwise pass here and
        only fail (silently, into a FAILED task) at approval-dispatch time.
        Dependencies must be unique and must not include the item itself.

        Returns:
            ``self`` unchanged when the id is a canonical UUID and the
            dependency list is free of self-references and duplicates.

        Raises:
            ValueError: When the id is not a canonical UUID, the item depends
                on itself, or a dependency is listed more than once.
        """
        try:
            canonical = str(UUID(self.id))
        except ValueError as exc:
            msg = f"Plan item id {self.id!r} must be a canonical UUID string"
            raise ValueError(msg) from exc
        if self.id != canonical:
            msg = f"Plan item id {self.id!r} is not in canonical UUID form"
            raise ValueError(msg)
        if self.id in self.dependencies:
            msg = f"Plan item {self.id!r} cannot depend on itself"
            raise ValueError(msg)
        if len(self.dependencies) != len(set(self.dependencies)):
            dupes = sorted(d for d, c in Counter(self.dependencies).items() if c > 1)
            msg = f"Plan item {self.id!r} has duplicate dependencies: {dupes}"
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
    created_at: AwareDatetime = Field(description="Creation timestamp (tz-aware UTC)")
    updated_at: AwareDatetime = Field(
        description="Last-revision timestamp (tz-aware UTC)"
    )

    @model_validator(mode="after")
    def _validate_items(self) -> Self:
        """Validate the item DAG (non-empty, unique, resolvable, acyclic).

        The items form a dependency DAG that dispatch walks in topological
        order, so a cycle is an entity invariant, not a downstream concern:
        it is rejected here (via a topological sort) rather than surfacing as
        a generic dispatch failure at approval time.

        Returns:
            ``self`` unchanged when items are non-empty, ids are unique, every
            dependency points to a known item, and the graph is acyclic.

        Raises:
            ValueError: When ``items`` is empty, ids duplicate, a dependency
                references an unknown item id, or the graph contains a cycle.
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
        self._reject_dependency_cycle()
        return self

    def _reject_dependency_cycle(self) -> None:
        """Reject a plan whose item dependency graph contains a cycle.

        Raises:
            ValueError: When the items cannot be ordered topologically (Kahn's
                algorithm leaves at least one item with unresolved
                dependencies), naming the items caught in the cycle.
        """
        pending = {item.id: set(item.dependencies) for item in self.items}
        while True:
            ready = {item_id for item_id, deps in pending.items() if not deps}
            if not ready:
                break
            for item_id in ready:
                del pending[item_id]
            for deps in pending.values():
                deps.difference_update(ready)
        if pending:
            cyclic = sorted(pending)
            msg = f"plan items form a dependency cycle: {cyclic}"
            raise ValueError(msg)
