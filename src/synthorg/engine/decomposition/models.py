"""Decomposition domain models.

Frozen Pydantic models for subtask definitions, decomposition plans,
results, status rollups, and decomposition context.
"""

from collections import Counter
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.enums import (
    Complexity,
    CoordinationTopology,
    Stakes,
    TaskStatus,
    TaskStructure,
)
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr


class SubtaskDefinition(BaseModel):
    """Definition of a single subtask within a decomposition plan.

    Attributes:
        id: Unique subtask identifier (within this decomposition).
        title: Short subtask title.
        description: Detailed subtask description.
        dependencies: IDs of other subtasks this one depends on.
        estimated_complexity: Complexity estimate for routing.
        stakes: Stakes level for stakes-aware model routing.
        required_skills: Skill IDs needed for routing.
        required_tags: Tags needed for multi-faceted routing match.  When
            set, the routing scorer awards a small bonus to agents whose
            matched-skill tags cover every required tag.  Empty tuple
            disables the tag-match tier.
        required_role: Optional role name for routing.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique subtask identifier")
    title: NotBlankStr = Field(description="Short subtask title")
    description: NotBlankStr = Field(description="Detailed subtask description")
    dependencies: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="IDs of subtasks this one depends on",
    )
    estimated_complexity: Complexity = Field(
        default=Complexity.MEDIUM,
        description="Complexity estimate for routing",
    )
    stakes: Stakes = Field(
        default=Stakes.NORMAL,
        description="Stakes level for stakes-aware model routing",
    )
    required_skills: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Skill IDs needed for routing",
    )
    required_tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Tags needed for multi-faceted routing match",
    )
    required_role: NotBlankStr | None = Field(
        default=None,
        description="Optional role name for routing",
    )

    @model_validator(mode="after")
    def _validate_no_self_dependency(self) -> Self:
        """Ensure subtask does not depend on itself.

        Returns:
            ``self`` unchanged when no self-cycle exists.

        Raises:
            ValueError: When the subtask's id appears in its
                ``dependencies`` tuple.
        """
        if self.id in self.dependencies:
            msg = f"Subtask {self.id!r} cannot depend on itself"
            raise ValueError(msg)
        return self


class DecompositionPlan(BaseModel):
    """Plan describing how a parent task is decomposed into subtasks.

    Validates subtask collection integrity at construction:
    non-empty, unique IDs, valid dependency references.
    Cycle detection is handled by ``DependencyGraph.validate()``
    in the service layer.

    Attributes:
        parent_task_id: ID of the task being decomposed.
        subtasks: Ordered subtask definitions.
        task_structure: Classified structure of the subtask graph.
        coordination_topology: Selected coordination topology.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    parent_task_id: NotBlankStr = Field(
        description="ID of the task being decomposed",
    )
    subtasks: tuple[SubtaskDefinition, ...] = Field(
        description="Ordered subtask definitions",
    )
    task_structure: TaskStructure = Field(
        default=TaskStructure.SEQUENTIAL,
        description="Classified task structure",
    )
    coordination_topology: CoordinationTopology = Field(
        default=CoordinationTopology.AUTO,
        description="Selected coordination topology",
    )

    @model_validator(mode="after")
    def _validate_subtasks(self) -> Self:
        """Validate subtask collection integrity.

        Returns:
            ``self`` unchanged when subtasks are non-empty, IDs are
            unique, and every dependency points to a known subtask.

        Raises:
            ValueError: When ``subtasks`` is empty, ids duplicate, or
                a subtask depends on an unknown id.
        """
        if not self.subtasks:
            msg = "subtasks must contain at least one entry"
            raise ValueError(msg)

        # Unique IDs
        ids = [s.id for s in self.subtasks]
        if len(ids) != len(set(ids)):
            dupes = sorted(i for i, c in Counter(ids).items() if c > 1)
            msg = f"Duplicate subtask IDs: {dupes}"
            raise ValueError(msg)

        # All dependency references must exist within subtasks
        id_set = set(ids)
        for subtask in self.subtasks:
            missing = [d for d in subtask.dependencies if d not in id_set]
            if missing:
                msg = (
                    f"Subtask {subtask.id!r} references unknown dependencies: {missing}"
                )
                raise ValueError(msg)

        return self


class DecompositionResult(BaseModel):
    """Result of a complete task decomposition.

    Attributes:
        plan: The decomposition plan that was executed.
        created_tasks: Task objects created from subtask definitions.
        dependency_edges: Directed edges (from_id, to_id) in the DAG.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    plan: DecompositionPlan = Field(description="Executed decomposition plan")
    created_tasks: tuple[Task, ...] = Field(
        description="Task objects created from subtask definitions",
    )
    dependency_edges: tuple[tuple[NotBlankStr, NotBlankStr], ...] = Field(
        default=(),
        description="Directed edges (from_id, to_id) in the DAG",
    )

    @model_validator(mode="after")
    def _validate_plan_task_consistency(self) -> Self:
        """Ensure created_tasks align with plan subtasks.

        Returns:
            ``self`` unchanged when tasks and edges are consistent with
            the plan.

        Raises:
            ValueError: When the task count mismatches, task ids
                differ from plan subtask ids, or an edge endpoint is
                an unknown task id.
        """
        if len(self.created_tasks) != len(self.plan.subtasks):
            msg = (
                f"created_tasks count ({len(self.created_tasks)}) "
                f"does not match plan subtask count "
                f"({len(self.plan.subtasks)})"
            )
            raise ValueError(msg)

        task_ids = {t.id for t in self.created_tasks}
        plan_ids = {s.id for s in self.plan.subtasks}
        if task_ids != plan_ids:
            missing = sorted(plan_ids - task_ids)
            extra = sorted(task_ids - plan_ids)
            msg = (
                f"created_tasks IDs do not match plan subtask IDs"
                f" (missing={missing}, extra={extra})"
            )
            raise ValueError(msg)

        edge_ids = {eid for edge in self.dependency_edges for eid in edge}
        unknown_edge_ids = edge_ids - task_ids
        if unknown_edge_ids:
            msg = (
                f"dependency_edges reference unknown task IDs: "
                f"{sorted(unknown_edge_ids)}"
            )
            raise ValueError(msg)

        return self


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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

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

    @computed_field(  # type: ignore[prop-decorator]
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


class DecompositionContext(BaseModel):
    """Configuration context for a decomposition operation.

    Attributes:
        max_subtasks: Maximum number of subtasks allowed.
        max_depth: Maximum nesting depth for recursive decomposition.
        current_depth: Current nesting depth.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_subtasks: int = Field(
        default=10,
        ge=1,
        description="Maximum number of subtasks allowed",
    )
    max_depth: int = Field(
        default=3,
        ge=1,
        description="Maximum nesting depth",
    )
    current_depth: int = Field(
        default=0,
        ge=0,
        description="Current nesting depth",
    )
