"""Tests for decomposition domain models."""

from typing import TYPE_CHECKING

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.plan import PlanOption
from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.task_enums import (
    Complexity,
    CoordinationTopology,
    Priority,
    TaskStatus,
    TaskStructure,
    TaskType,
)
from tests._shared import as_uuid, sid
from tests._shared.scripted_provider import make_e2e_identity

if TYPE_CHECKING:
    from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import (
    DecompositionContext,
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
    SubtaskStatusRollup,
    roster_from_agents,
)


def _sub(
    subtask_id: str,
    *,
    title: str = "A",
    description: str = "A desc",
    dependencies: tuple[str, ...] = (),
) -> SubtaskDefinition:
    """A WORK subtask carrying the deliverable a dispatchable plan requires."""
    return SubtaskDefinition(
        id=subtask_id,
        title=title,
        description=description,
        dependencies=dependencies,
        expected_artifacts=(f"src/{subtask_id}.py",),
    )


# ---------------------------------------------------------------------------
# SubtaskDefinition
# ---------------------------------------------------------------------------


class TestSubtaskDefinition:
    """Tests for SubtaskDefinition model."""

    @pytest.mark.unit
    def test_minimal_subtask(self) -> None:
        """Minimal subtask with only required fields."""
        sub = SubtaskDefinition(
            id="sub-1",
            title="Do thing",
            description="Detailed description",
        )
        assert sub.id == "sub-1"
        assert sub.dependencies == ()
        assert sub.required_skills == ()
        assert sub.required_role is None
        assert sub.estimated_complexity == Complexity.MEDIUM

    @pytest.mark.unit
    def test_subtask_with_all_fields(self) -> None:
        """Subtask with all optional fields populated."""
        sub = SubtaskDefinition(
            id="sub-1",
            title="Do thing",
            description="Detailed",
            dependencies=("sub-0",),
            estimated_complexity=Complexity.COMPLEX,
            required_skills=("python", "sql"),
            required_role="backend-developer",
        )
        assert sub.dependencies == ("sub-0",)
        assert sub.required_skills == ("python", "sql")
        assert sub.required_role == "backend-developer"
        assert sub.estimated_complexity == Complexity.COMPLEX

    @pytest.mark.unit
    def test_self_dependency_rejected(self) -> None:
        """Subtask cannot depend on itself."""
        with pytest.raises(ValueError, match="cannot depend on itself"):
            SubtaskDefinition(
                id="sub-1",
                title="Do thing",
                description="Detailed",
                dependencies=("sub-1",),
            )

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """SubtaskDefinition is immutable."""
        sub = SubtaskDefinition(id="sub-1", title="Do thing", description="Detailed")
        with pytest.raises(Exception, match="frozen"):
            sub.id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DecompositionPlan
# ---------------------------------------------------------------------------


class TestDecompositionPlan:
    """Tests for DecompositionPlan model."""

    @pytest.mark.unit
    def test_valid_plan(self) -> None:
        """A valid plan with sequential subtasks."""
        plan = DecompositionPlan(
            parent_task_id="task-1",
            subtasks=(
                _sub("sub-1", title="First", description="First step"),
                _sub(
                    "sub-2",
                    title="Second",
                    description="Second step",
                    dependencies=("sub-1",),
                ),
            ),
        )
        assert plan.parent_task_id == "task-1"
        assert len(plan.subtasks) == 2
        # AUTO means the planner declared nothing, which is what lets the
        # service tell that case apart from an explicit SEQUENTIAL.
        assert plan.task_structure is TaskStructure.AUTO
        assert plan.coordination_topology == CoordinationTopology.AUTO

    @pytest.mark.unit
    def test_empty_subtasks_rejected(self) -> None:
        """Plan must have at least one subtask."""
        with pytest.raises(ValueError, match="at least one entry"):
            DecompositionPlan(
                parent_task_id="task-1",
                subtasks=(),
            )

    @pytest.mark.unit
    def test_duplicate_ids_rejected(self) -> None:
        """Plan rejects duplicate subtask IDs."""
        with pytest.raises(ValueError, match="Duplicate subtask IDs"):
            DecompositionPlan(
                parent_task_id="task-1",
                subtasks=(
                    _sub("sub-1"),
                    _sub("sub-1", title="B", description="B desc"),
                ),
            )

    @pytest.mark.unit
    def test_unknown_dependency_rejected(self) -> None:
        """Plan rejects references to non-existent subtask IDs."""
        with pytest.raises(ValueError, match="unknown dependencies"):
            DecompositionPlan(
                parent_task_id="task-1",
                subtasks=(_sub("sub-1", dependencies=("sub-99",)),),
            )

    @pytest.mark.unit
    def test_cycle_accepted_at_plan_level(self) -> None:
        """Plan does not perform cycle detection (handled by DAG)."""
        # Cycles are caught by DependencyGraph.validate(), not by the plan
        plan = DecompositionPlan(
            parent_task_id="task-1",
            subtasks=(
                _sub("sub-1", dependencies=("sub-2",)),
                _sub("sub-2", title="B", description="B desc", dependencies=("sub-1",)),
            ),
        )
        assert len(plan.subtasks) == 2

    @pytest.mark.unit
    def test_work_subtask_without_artifacts_rejected(self) -> None:
        """A dispatchable WORK subtask must declare a deliverable."""
        with pytest.raises(ValueError, match="must declare at least one expected"):
            DecompositionPlan(
                parent_task_id="task-1",
                subtasks=(
                    SubtaskDefinition(id="sub-1", title="A", description="A desc"),
                ),
            )

    @pytest.mark.unit
    def test_decision_subtask_with_artifacts_rejected(self) -> None:
        """A decision records a choice, so it never declares a deliverable."""
        with pytest.raises(ValueError, match=r"DECISION .* declares expected"):
            DecompositionPlan(
                parent_task_id="task-1",
                subtasks=(
                    SubtaskDefinition(
                        id="sub-1",
                        title="Pick a stack",
                        description="React or Svelte",
                        kind=PlanItemKind.DECISION,
                        options=(
                            PlanOption(
                                id="react",
                                title="React",
                                summary="Mature ecosystem",
                                recommended=True,
                            ),
                            PlanOption(
                                id="svelte", title="Svelte", summary="Smaller bundle"
                            ),
                        ),
                        expected_artifacts=("src/app.tsx",),
                    ),
                ),
            )

    @pytest.mark.unit
    def test_decision_subtask_without_artifacts_accepted(self) -> None:
        """A well-formed decision subtask needs no deliverable."""
        plan = DecompositionPlan(
            parent_task_id="task-1",
            subtasks=(
                SubtaskDefinition(
                    id="sub-1",
                    title="Pick a stack",
                    description="React or Svelte",
                    kind=PlanItemKind.DECISION,
                    options=(
                        PlanOption(
                            id="react",
                            title="React",
                            summary="Mature ecosystem",
                            recommended=True,
                        ),
                        PlanOption(
                            id="svelte", title="Svelte", summary="Smaller bundle"
                        ),
                    ),
                ),
            ),
        )
        assert plan.subtasks[0].expected_artifacts == ()


# ---------------------------------------------------------------------------
# DecompositionResult
# ---------------------------------------------------------------------------


def _make_result_task(subtask_id: str) -> Task:
    """Helper to create a minimal task for result construction."""
    from synthorg.core.task import Task

    return Task(
        id=as_uuid(subtask_id),
        title=f"Subtask {subtask_id}",
        description=f"Description for {subtask_id}",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="creator",
    )


class TestDecompositionResult:
    """Tests for DecompositionResult model."""

    @pytest.mark.unit
    def test_valid_result(self, sample_task_with_criteria: Task) -> None:
        """DecompositionResult holds plan, tasks, and edges."""
        plan = DecompositionPlan(
            parent_task_id=str(sample_task_with_criteria.id),
            subtasks=(_sub(sid("sub-1")),),
            task_structure=TaskStructure.SEQUENTIAL,
        )
        child_task = _make_result_task("sub-1")
        result = DecompositionResult(
            plan=plan,
            created_tasks=(child_task,),
            dependency_edges=(),
        )
        assert result.plan is plan
        assert result.dependency_edges == ()
        assert len(result.created_tasks) == 1

    @pytest.mark.unit
    def test_unresolved_task_structure_rejected(
        self, sample_task_with_criteria: Task
    ) -> None:
        """A completed decomposition always names its structure.

        The service resolves an undeclared one through the classifier, so a
        result still carrying AUTO never came through it.
        """
        plan = DecompositionPlan(
            parent_task_id=str(sample_task_with_criteria.id),
            subtasks=(_sub(sid("sub-1")),),
        )
        assert plan.task_structure is TaskStructure.AUTO
        with pytest.raises(ValueError, match="resolved plan task_structure"):
            DecompositionResult(
                plan=plan,
                created_tasks=(_make_result_task("sub-1"),),
                dependency_edges=(),
            )

    @pytest.mark.unit
    def test_task_count_mismatch_rejected(self) -> None:
        """Result rejects mismatched task count vs plan subtasks."""
        plan = DecompositionPlan(
            parent_task_id="task-1",
            subtasks=(
                _sub("sub-1"),
                _sub("sub-2", title="B", description="B desc"),
            ),
            task_structure=TaskStructure.SEQUENTIAL,
        )
        with pytest.raises(ValueError, match="does not match plan subtask count"):
            DecompositionResult(
                plan=plan,
                created_tasks=(_make_result_task("sub-1"),),
                dependency_edges=(),
            )

    @pytest.mark.unit
    def test_task_id_mismatch_rejected(self) -> None:
        """Result rejects matching count but different IDs."""
        plan = DecompositionPlan(
            parent_task_id="task-1",
            subtasks=(
                _sub(sid("sub-1")),
                _sub(sid("sub-2"), title="B", description="B desc"),
            ),
            task_structure=TaskStructure.SEQUENTIAL,
        )
        with pytest.raises(
            ValueError,
            match=rf"missing=\['{sid('sub-2')}'\].*extra=\['{sid('sub-99')}'\]",
        ):
            DecompositionResult(
                plan=plan,
                created_tasks=(
                    _make_result_task("sub-1"),
                    _make_result_task("sub-99"),
                ),
                dependency_edges=(),
            )

    @pytest.mark.unit
    def test_unknown_edge_ids_rejected(self) -> None:
        """Result rejects edges referencing unknown task IDs."""
        plan = DecompositionPlan(
            parent_task_id="task-1",
            subtasks=(_sub(sid("sub-1")),),
            task_structure=TaskStructure.SEQUENTIAL,
        )
        with pytest.raises(ValueError, match="unknown task IDs"):
            DecompositionResult(
                plan=plan,
                created_tasks=(_make_result_task("sub-1"),),
                dependency_edges=((sid("sub-1"), sid("sub-99")),),
            )


# ---------------------------------------------------------------------------
# SubtaskStatusRollup
# ---------------------------------------------------------------------------


class TestSubtaskStatusRollup:
    """Tests for SubtaskStatusRollup model."""

    @pytest.mark.unit
    def test_all_completed(self) -> None:
        """All completed -> COMPLETED status."""
        rollup = SubtaskStatusRollup(
            parent_task_id="task-1",
            total=3,
            completed=3,
            failed=0,
            in_progress=0,
            blocked=0,
            cancelled=0,
        )
        assert rollup.derived_parent_status == TaskStatus.COMPLETED

    @pytest.mark.unit
    def test_any_failed(self) -> None:
        """Any failed -> FAILED status."""
        rollup = SubtaskStatusRollup(
            parent_task_id="task-1",
            total=3,
            completed=1,
            failed=1,
            in_progress=0,
            blocked=0,
            cancelled=0,
        )
        assert rollup.derived_parent_status == TaskStatus.FAILED

    @pytest.mark.unit
    def test_any_in_progress(self) -> None:
        """Any in progress -> IN_PROGRESS status."""
        rollup = SubtaskStatusRollup(
            parent_task_id="task-1",
            total=3,
            completed=1,
            failed=0,
            in_progress=1,
            blocked=0,
            cancelled=0,
        )
        assert rollup.derived_parent_status == TaskStatus.IN_PROGRESS

    @pytest.mark.unit
    def test_all_cancelled(self) -> None:
        """All cancelled -> CANCELLED status."""
        rollup = SubtaskStatusRollup(
            parent_task_id="task-1",
            total=2,
            completed=0,
            failed=0,
            in_progress=0,
            blocked=0,
            cancelled=2,
        )
        assert rollup.derived_parent_status == TaskStatus.CANCELLED

    @pytest.mark.unit
    def test_any_blocked_no_in_progress(self) -> None:
        """Blocked with no in-progress -> BLOCKED status."""
        rollup = SubtaskStatusRollup(
            parent_task_id="task-1",
            total=3,
            completed=1,
            failed=0,
            in_progress=0,
            blocked=1,
            cancelled=0,
        )
        assert rollup.derived_parent_status == TaskStatus.BLOCKED

    @pytest.mark.unit
    def test_empty_total(self) -> None:
        """Zero total -> CREATED status."""
        rollup = SubtaskStatusRollup(
            parent_task_id="task-1",
            total=0,
            completed=0,
            failed=0,
            in_progress=0,
            blocked=0,
            cancelled=0,
        )
        assert rollup.derived_parent_status == TaskStatus.CREATED

    @pytest.mark.unit
    def test_counts_exceed_total_rejected(self) -> None:
        """Sum of counts exceeding total is rejected."""
        with pytest.raises(ValueError, match="exceeds total"):
            SubtaskStatusRollup(
                parent_task_id="task-1",
                total=2,
                completed=3,
                failed=0,
                in_progress=0,
                blocked=0,
                cancelled=0,
            )

    @pytest.mark.unit
    def test_pending_work_defaults_to_in_progress(self) -> None:
        """When some subtasks are not yet categorized -> IN_PROGRESS."""
        rollup = SubtaskStatusRollup(
            parent_task_id="task-1",
            total=5,
            completed=1,
            failed=0,
            in_progress=0,
            blocked=0,
            cancelled=0,
        )
        assert rollup.derived_parent_status == TaskStatus.IN_PROGRESS

    @pytest.mark.unit
    def test_completed_plus_cancelled_mix(self) -> None:
        """Fully terminal mix of COMPLETED+CANCELLED -> CANCELLED.

        When some subtasks were cancelled, the parent cannot be considered
        fully completed -- CANCELLED signals partial abandonment.
        """
        rollup = SubtaskStatusRollup(
            parent_task_id="task-1",
            total=5,
            completed=3,
            failed=0,
            in_progress=0,
            blocked=0,
            cancelled=2,
        )
        assert rollup.derived_parent_status == TaskStatus.CANCELLED


# ---------------------------------------------------------------------------
# DecompositionContext
# ---------------------------------------------------------------------------


class TestDecompositionContext:
    """Tests for DecompositionContext model."""

    @pytest.mark.unit
    def test_default_values(self) -> None:
        """Default context is depth=0, max_depth=3, max_subtasks=10."""
        ctx = DecompositionContext()
        assert ctx.max_subtasks == 10
        assert ctx.max_depth == 3
        assert ctx.current_depth == 0

    @pytest.mark.unit
    def test_depth_at_max_allowed(self) -> None:
        """Context allows current_depth == max_depth (policy is in strategy)."""
        ctx = DecompositionContext(current_depth=3, max_depth=3)
        assert ctx.current_depth == 3

    @pytest.mark.unit
    def test_valid_depth(self) -> None:
        """Context allows current_depth < max_depth."""
        ctx = DecompositionContext(current_depth=2, max_depth=3)
        assert ctx.current_depth == 2

    @pytest.mark.unit
    def test_zero_max_subtasks_rejected(self) -> None:
        """max_subtasks must be >= 1."""
        with pytest.raises(ValueError, match="greater than or equal to 1"):
            DecompositionContext(max_subtasks=0)

    @pytest.mark.unit
    def test_zero_max_depth_rejected(self) -> None:
        """max_depth must be >= 1."""
        with pytest.raises(ValueError, match="greater than or equal to 1"):
            DecompositionContext(max_depth=0)

    @pytest.mark.unit
    def test_negative_current_depth_rejected(self) -> None:
        """current_depth must be >= 0."""
        with pytest.raises(ValueError, match="greater than or equal to 0"):
            DecompositionContext(current_depth=-1)

    @pytest.mark.unit
    def test_a_role_is_flattened_on_the_way_in(self) -> None:
        """The roster reaches the SYSTEM prompt and the tool schema.

        A newline in a role name opens a fresh instruction line there and
        angle brackets forge a content fence, so the field type refuses to
        hold either shape in the first place.
        """
        # Passed raw: what the validator does to it is the assertion.
        ctx = DecompositionContext(available_roles=("Backend\nDeveloper <admin>",))

        assert ctx.available_roles == ("Backend Developer admin",)


class TestRosterFromAgents:
    """The roster is what the planner is allowed to assign work to."""

    @staticmethod
    def _agent(role: str) -> AgentIdentity:
        return make_e2e_identity(label=f"agent-{role}").model_copy(
            update={"role": NotBlankStr(role)}
        )

    @pytest.mark.unit
    def test_a_role_several_agents_hold_is_listed_once(self) -> None:
        """Repeating it would just spend prompt on the same instruction."""
        roster = roster_from_agents(
            [self._agent("Backend Developer"), self._agent("Backend Developer")]
        )

        assert roster == ("Backend Developer",)

    @pytest.mark.unit
    def test_the_order_does_not_depend_on_the_agent_order(self) -> None:
        """The prompt and the schema enum are pinned by fingerprint tests."""
        forwards = roster_from_agents(
            [self._agent("Writer"), self._agent("Backend Developer")]
        )
        backwards = roster_from_agents(
            [self._agent("Backend Developer"), self._agent("Writer")]
        )

        assert forwards == backwards == ("Backend Developer", "Writer")

    @pytest.mark.unit
    def test_no_agents_means_no_roster(self) -> None:
        """Empty is "no roster known", which leaves the owner a free string."""
        assert roster_from_agents([]) == ()
