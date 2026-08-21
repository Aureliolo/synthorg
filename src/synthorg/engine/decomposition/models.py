"""Decomposition domain models.

Frozen Pydantic models for subtask definitions, decomposition plans and the
decomposition tree. The context a decomposition runs under lives in
:mod:`synthorg.engine.decomposition.context`, and what its execution adds up to
is a different question again, in
:mod:`synthorg.engine.decomposition.status_rollup`.
"""

from collections import Counter
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.plan import PlanOption
from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.plan_validation import (
    validate_decision_options,
    validate_expected_artifacts,
)
from synthorg.core.task import Task
from synthorg.core.task_enums import (
    Complexity,
    CoordinationTopology,
    Stakes,
    TaskStructure,
)
from synthorg.core.types import NotBlankStr


class SubtaskDefinition(BaseModel):
    """Definition of a single subtask within a decomposition plan.

    Attributes:
        id: Unique subtask identifier (within this decomposition).
        title: Short subtask title.
        description: Detailed subtask description.
        dependencies: IDs of other subtasks this one depends on.
        estimated_complexity: Complexity estimate for routing.
        stakes: Stakes level for capability-based agent selection.
        required_skills: Skill IDs needed for routing.
        required_tags: Tags needed for multi-faceted routing match.  When
            set, the routing scorer awards a small bonus to agents whose
            matched-skill tags cover every required tag.  Empty tuple
            disables the tag-match tier.
        required_role: Optional role name for routing.
        expected_artifacts: Deliverables this subtask must produce.  These
            project onto the dispatched task's ``artifacts_expected`` and arm
            the fail-loud zero-artifact guard, so the subtask cannot terminate
            a success having produced nothing.  A subtask that reaches a
            :class:`DecompositionPlan` must declare one (see that model's
            validator); the field stays optional here because the routing
            scorer also builds a bare, never-dispatched proxy definition.
        acceptance_criteria: Per-subtask criteria that define "done" for it.
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
        description="Stakes level for capability-based agent selection",
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
    expected_artifacts: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Deliverables this subtask must produce",
    )
    acceptance_criteria: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Per-subtask criteria that define done",
    )
    satisfies: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Objective success criteria this subtask advances",
    )
    kind: PlanItemKind = Field(
        default=PlanItemKind.WORK,
        description="Whether this subtask is work to execute or a decision point",
    )
    options: tuple[PlanOption, ...] = Field(
        default=(),
        description="For a DECISION subtask, the options to choose among",
    )

    @model_validator(mode="after")
    def _validate_subtask(self) -> Self:
        """Reject a self-dependency and enforce the decision-option shape.

        Returns:
            ``self`` unchanged when the subtask does not depend on itself and
            its WORK/DECISION option shape is valid.

        Raises:
            ValueError: When the subtask depends on itself, or its option shape
                is invalid (see :func:`validate_decision_options`).
        """
        if self.id in self.dependencies:
            msg = f"Subtask {self.id!r} cannot depend on itself"
            raise ValueError(msg)
        validate_decision_options(
            entity_id=self.id, kind=self.kind, options=self.options
        )
        return self


class DecompositionPlan(BaseModel):
    """Plan describing how a parent task is decomposed into subtasks.

    Validates subtask collection integrity at construction:
    non-empty, unique IDs, valid dependency references, and a declared
    deliverable per WORK subtask.
    Cycle detection is handled by ``DependencyGraph.validate()``
    in the service layer.

    Attributes:
        parent_task_id: ID of the task being decomposed.
        subtasks: Ordered subtask definitions.
        task_structure: Structure the planner declared, or ``AUTO`` when it
            declared none. ``DecompositionService`` resolves ``AUTO`` through
            the classifier before the plan leaves the service, so every plan
            reaching a :class:`DecompositionResult` names its structure.
        coordination_topology: Selected coordination topology.
        planning_strategy: Which planner produced this plan. Blank means
            the strategy did not say; a fallback always says, so the
            approval gate can show the operator that what they are being
            asked to approve is a single-shot substitute rather than the
            researched plan the owner was asked for.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    parent_task_id: NotBlankStr = Field(
        description="ID of the task being decomposed",
    )
    subtasks: tuple[SubtaskDefinition, ...] = Field(
        description="Ordered subtask definitions",
    )
    task_structure: TaskStructure = Field(
        default=TaskStructure.AUTO,
        description=(
            "Structure the planner declared; AUTO means it declared nothing and"
            " the classifier heuristic decides"
        ),
    )
    coordination_topology: CoordinationTopology = Field(
        default=CoordinationTopology.AUTO,
        description="Selected coordination topology",
    )
    open_questions: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Unresolved questions the planner surfaced for the human",
    )
    assumptions: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Assumptions the plan rests on",
    )
    planning_strategy: NotBlankStr | None = Field(
        default=None,
        description="Which planner produced this plan; set when a fallback "
        "produced it so the substitution is visible on the durable plan",
    )

    @model_validator(mode="after")
    def _validate_subtasks(self) -> Self:
        """Validate subtask collection integrity.

        The deliverable invariant lives here rather than on
        :class:`SubtaskDefinition` because a plan is precisely the set of
        subtasks that will be dispatched, whereas a bare definition is also
        built as a throwaway scoring proxy by the routing layer, which has no
        deliverable to declare.

        Returns:
            ``self`` unchanged when subtasks are non-empty, IDs are
            unique, every dependency points to a known subtask, and every
            WORK subtask declares a deliverable.

        Raises:
            ValueError: When ``subtasks`` is empty, ids duplicate, a subtask
                depends on an unknown id, or a subtask's declared deliverables
                contradict its kind.
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
            validate_expected_artifacts(
                entity_id=subtask.id,
                kind=subtask.kind,
                expected_artifacts=subtask.expected_artifacts,
            )

        return self


class DecompositionResult(BaseModel):
    """Result of a complete task decomposition.

    One level of a decomposition. A subtask the atomicity policy judged
    oversized is decomposed again, and its own result hangs off ``children``,
    so the whole shape is a tree rather than a list.

    ``children`` defaults to empty, which is exactly what a non-recursive
    decomposition produces, so every reader that predates recursion sees the
    flat result it always saw.

    Attributes:
        plan: The decomposition plan that was executed.
        created_tasks: Task objects created from subtask definitions.
        dependency_edges: Directed edges (from_id, to_id) in the DAG.
        depth: This level's nesting depth, ``0`` at the root. Recorded rather
            than derived by the reader, because the reader most likely to want
            it holds one node and not the tree it came from.
        children: The decomposition of each subtask at this level that was
            split further, in no particular relation to ``created_tasks``
            order.
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
    depth: int = Field(
        default=0,
        ge=0,
        description="Nesting depth of this level, 0 at the root",
    )
    children: tuple[DecompositionResult, ...] = Field(
        default=(),
        description="Decompositions of the subtasks at this level that split",
    )

    @property
    def split_task_ids(self) -> frozenset[str]:
        """Ids of this level's tasks that were decomposed further.

        Returns:
            The parent task id of each child decomposition.
        """
        return frozenset(child.plan.parent_task_id for child in self.children)

    @property
    def leaf_tasks(self) -> tuple[Task, ...]:
        """Every task in the tree that nothing below it replaced.

        This is what gets dispatched: a task that was split is a container for
        the work below it, and running it as well would do that work twice.

        Returns:
            The leaves, this level's first, then each child's in order.
        """
        split = self.split_task_ids
        own = tuple(task for task in self.created_tasks if str(task.id) not in split)
        return own + tuple(task for child in self.children for task in child.leaf_tasks)

    @property
    def all_tasks(self) -> tuple[Task, ...]:
        """Every task in the tree, split containers included.

        Returns:
            This level's tasks, then each child's, recursively.
        """
        return self.created_tasks + tuple(
            task for child in self.children for task in child.all_tasks
        )

    @property
    def max_depth_reached(self) -> int:
        """The deepest level this tree actually reached.

        The measured counterpart of ``DecompositionContext.max_depth``, which
        is only a ceiling: a planner that never produced an oversized subtask
        stops well short of it.

        Returns:
            ``depth`` when nothing split, else the deepest child's answer.
        """
        return max(
            (child.max_depth_reached for child in self.children), default=self.depth
        )

    @model_validator(mode="after")
    def _validate_plan_task_consistency(self) -> Self:
        """Ensure created_tasks align with plan subtasks.

        Returns:
            ``self`` unchanged when tasks and edges are consistent with
            the plan and the plan's structure has been resolved.

        Raises:
            ValueError: When the structure is unresolved, the task count
                mismatches, task ids differ from plan subtask ids, an
                edge endpoint is an unknown task id, or a child decomposes
                something this level did not create.
        """
        # A completed decomposition always names its structure: the service
        # resolves an undeclared one through the classifier. Leaving AUTO
        # reachable here would let it sequentialise silently downstream.
        if self.plan.task_structure is TaskStructure.AUTO:
            msg = "DecompositionResult requires a resolved plan task_structure"
            raise ValueError(msg)

        if len(self.created_tasks) != len(self.plan.subtasks):
            msg = (
                f"created_tasks count ({len(self.created_tasks)}) "
                f"does not match plan subtask count "
                f"({len(self.plan.subtasks)})"
            )
            raise ValueError(msg)

        task_ids = {str(t.id) for t in self.created_tasks}
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

        self._validate_children(task_ids)
        return self

    def _validate_children(self, task_ids: set[str]) -> None:
        """Check each child decomposes a task this level created, one level down.

        A child whose parent is not one of these tasks describes a subtree that
        hangs off nothing, and ``leaf_tasks`` would then dispatch both the
        container and the work below it.

        Raises:
            ValueError: When a child names an unknown parent, two children name
                the same one, or a child's depth is not this level's plus one.
        """
        seen: set[str] = set()
        for child in self.children:
            parent = child.plan.parent_task_id
            if parent not in task_ids:
                msg = (
                    f"child decomposition names parent {parent!r}, which is not "
                    f"one of this level's tasks: {sorted(task_ids)}"
                )
                raise ValueError(msg)
            if parent in seen:
                msg = f"two child decompositions both name parent {parent!r}"
                raise ValueError(msg)
            seen.add(parent)
            if child.depth != self.depth + 1:
                msg = (
                    f"child decomposition of {parent!r} is at depth "
                    f"{child.depth}, expected {self.depth + 1}"
                )
                raise ValueError(msg)
