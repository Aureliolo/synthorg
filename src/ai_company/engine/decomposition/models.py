"""Decomposition domain models.

Frozen Pydantic models for subtask definitions, decomposition plans,
results, status rollups, and decomposition context.
"""

from collections import Counter
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from ai_company.core.enums import (
    CoordinationTopology,
    TaskStatus,
    TaskStructure,
)
from ai_company.core.task import Task  # noqa: TC001
from ai_company.core.types import NotBlankStr  # noqa: TC001


class SubtaskDefinition(BaseModel):
    """Definition of a single subtask within a decomposition plan.

    Attributes:
        id: Unique subtask identifier (within this decomposition).
        title: Short subtask title.
        description: Detailed subtask description.
        dependencies: IDs of other subtasks this one depends on.
        estimated_complexity: Free-text complexity estimate.
        required_skills: Skill names needed for routing.
        required_role: Optional role name for routing.
    """

    model_config = ConfigDict(frozen=True)

    id: NotBlankStr = Field(description="Unique subtask identifier")
    title: NotBlankStr = Field(description="Short subtask title")
    description: NotBlankStr = Field(description="Detailed subtask description")
    dependencies: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="IDs of subtasks this one depends on",
    )
    estimated_complexity: NotBlankStr = Field(
        default="medium",
        description="Free-text complexity estimate",
    )
    required_skills: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Skill names needed for routing",
    )
    required_role: NotBlankStr | None = Field(
        default=None,
        description="Optional role name for routing",
    )

    @model_validator(mode="after")
    def _validate_no_self_dependency(self) -> Self:
        """Ensure subtask does not depend on itself."""
        if self.id in self.dependencies:
            msg = f"Subtask {self.id!r} cannot depend on itself"
            raise ValueError(msg)
        return self


class DecompositionPlan(BaseModel):
    """Plan describing how a parent task is decomposed into subtasks.

    Attributes:
        parent_task_id: ID of the task being decomposed.
        subtasks: Ordered subtask definitions.
        task_structure: Classified structure of the subtask graph.
        coordination_topology: Selected coordination topology.
    """

    model_config = ConfigDict(frozen=True)

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
        """Validate subtask collection integrity."""
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

        # Cycle detection via DFS
        self._detect_cycles(id_set)
        return self

    def _detect_cycles(self, id_set: set[str]) -> None:
        """Detect cycles in the subtask dependency graph using DFS."""
        dep_map: dict[str, tuple[str, ...]] = {
            s.id: s.dependencies for s in self.subtasks
        }
        visited: set[str] = set()
        in_stack: set[str] = set()

        def dfs(node: str) -> None:
            visited.add(node)
            in_stack.add(node)
            for dep in dep_map.get(node, ()):
                if dep in in_stack:
                    msg = f"Dependency cycle detected involving {dep!r}"
                    raise ValueError(msg)
                if dep not in visited:
                    dfs(dep)
            in_stack.discard(node)

        for sid in id_set:
            if sid not in visited:
                dfs(sid)


class DecompositionResult(BaseModel):
    """Result of a complete task decomposition.

    Attributes:
        plan: The decomposition plan that was executed.
        created_tasks: Task objects created from subtask definitions.
        dependency_edges: Directed edges (from_id, to_id) in the DAG.
    """

    model_config = ConfigDict(frozen=True)

    plan: DecompositionPlan = Field(description="Executed decomposition plan")
    created_tasks: tuple[Task, ...] = Field(
        description="Task objects created from subtask definitions",
    )
    dependency_edges: tuple[tuple[NotBlankStr, NotBlankStr], ...] = Field(
        default=(),
        description="Directed edges (from_id, to_id) in the DAG",
    )


class SubtaskStatusRollup(BaseModel):
    """Aggregated status of subtasks for a parent task.

    Attributes:
        parent_task_id: ID of the parent task.
        total: Total number of subtasks.
        completed: Count of COMPLETED subtasks.
        failed: Count of FAILED subtasks.
        in_progress: Count of IN_PROGRESS subtasks.
        blocked: Count of BLOCKED subtasks.
        cancelled: Count of CANCELLED subtasks.
    """

    model_config = ConfigDict(frozen=True)

    parent_task_id: NotBlankStr = Field(description="Parent task ID")
    total: int = Field(ge=0, description="Total subtasks")
    completed: int = Field(ge=0, description="Completed subtasks")
    failed: int = Field(ge=0, description="Failed subtasks")
    in_progress: int = Field(ge=0, description="In-progress subtasks")
    blocked: int = Field(ge=0, description="Blocked subtasks")
    cancelled: int = Field(ge=0, description="Cancelled subtasks")

    @model_validator(mode="after")
    def _validate_counts(self) -> Self:
        """Ensure counts don't exceed total."""
        counted = (
            self.completed
            + self.failed
            + self.in_progress
            + self.blocked
            + self.cancelled
        )
        if counted > self.total:
            msg = "Sum of status counts exceeds total"
            raise ValueError(msg)
        return self

    @computed_field(  # type: ignore[prop-decorator]
        description="Derived parent task status from subtask statuses",
    )
    @property
    def derived_parent_status(self) -> TaskStatus:  # noqa: PLR0911
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

        return TaskStatus.IN_PROGRESS


class DecompositionContext(BaseModel):
    """Configuration context for a decomposition operation.

    Attributes:
        max_subtasks: Maximum number of subtasks allowed.
        max_depth: Maximum nesting depth for recursive decomposition.
        current_depth: Current nesting depth.
    """

    model_config = ConfigDict(frozen=True)

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

    @model_validator(mode="after")
    def _validate_depth(self) -> Self:
        """Ensure current depth doesn't exceed max depth."""
        if self.current_depth >= self.max_depth:
            msg = (
                f"Current depth {self.current_depth} "
                f"has reached max depth {self.max_depth}"
            )
            raise ValueError(msg)
        return self
