"""Tests for the ``DecompositionResult`` -> ``Plan`` projection."""

from datetime import UTC, datetime

import pytest

from synthorg.core.artifact import ArtifactType
from synthorg.core.plan import Plan, PlanItem, PlanOption
from synthorg.core.plan_enums import (
    PlanItemKind,
    PlanReviewFindingCategory,
    PlanReviewVerdict,
    PlanStatus,
)
from synthorg.core.plan_review import (
    PlanReview,
    PlanReviewerVerdict,
    PlanReviewFinding,
)
from synthorg.core.task import Task
from synthorg.core.task_enums import (
    Complexity,
    CoordinationTopology,
    Priority,
    Stakes,
    TaskStructure,
    TaskType,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.decomposition.plan_mapping import (
    PlanProvenance,
    decomposition_from_plan,
    plan_from_decomposition,
)
from synthorg.engine.errors import DecompositionError
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_CREATED_AT = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def _provenance(
    *,
    status: PlanStatus = PlanStatus.PENDING_REVIEW,
    review: PlanReview | None = None,
    objective_criteria: tuple[NotBlankStr, ...] = (),
) -> PlanProvenance:
    return PlanProvenance(
        project=NotBlankStr("beachhead"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the game"),
        parent_task_id=sid("root"),
        created_at=_CREATED_AT,
        status=status,
        review=review,
        objective_criteria=objective_criteria,
    )


def _result_task(subtask_id: str) -> Task:
    return Task(
        id=as_uuid(subtask_id),
        title=f"Subtask {subtask_id}",
        description=f"Description for {subtask_id}",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="beachhead",
        created_by="ceo",
    )


def _decomposition() -> DecompositionResult:
    plan = DecompositionPlan(
        parent_task_id=sid("root"),
        subtasks=(
            SubtaskDefinition(
                id=sid("sub-1"),
                title="Board",
                description="Grid + rendering",
                estimated_complexity=Complexity.COMPLEX,
                stakes=Stakes.HIGH,
                required_role="engineering",
                acceptance_criteria=(NotBlankStr("board renders"),),
                expected_artifacts=(NotBlankStr("src/board.py"),),
            ),
            SubtaskDefinition(
                id=sid("sub-2"),
                title="Movement",
                description="Drop + rotate",
                dependencies=(sid("sub-1"),),
                acceptance_criteria=(NotBlankStr("pieces move"),),
                expected_artifacts=(NotBlankStr("src/movement.py"),),
            ),
        ),
        task_structure=TaskStructure.SEQUENTIAL,
        coordination_topology=CoordinationTopology.AUTO,
    )
    return DecompositionResult(
        plan=plan,
        created_tasks=(_result_task("sub-1"), _result_task("sub-2")),
    )


class TestPlanFromDecomposition:
    def test_maps_structure_and_items(self) -> None:
        plan = plan_from_decomposition(_decomposition(), _provenance())

        assert plan.project == "beachhead"
        assert plan.objective_id == "obj-1"
        assert plan.parent_task_id == sid("root")
        assert plan.status is PlanStatus.PENDING_REVIEW
        assert plan.task_structure is TaskStructure.SEQUENTIAL
        assert plan.coordination_topology is CoordinationTopology.AUTO
        assert plan.created_at == _CREATED_AT
        assert plan.updated_at == _CREATED_AT
        assert plan.version == 1

    def test_an_unresolved_structure_fails_loud(self) -> None:
        """A decomposition that skipped the service must not sequentialise.

        ``DecompositionResult`` rejects an unresolved structure, so this can
        only arrive by constructing the projection input by hand; substituting
        a default here would hide that the classifier never ran.
        """
        decomposition = _decomposition()
        unresolved = decomposition.model_copy(
            update={
                "plan": decomposition.plan.model_copy(
                    update={"task_structure": TaskStructure.AUTO},
                ),
            },
        )
        with pytest.raises(DecompositionError, match="unresolved task_structure"):
            plan_from_decomposition(unresolved, _provenance())

    def test_item_fields_projected(self) -> None:
        plan = plan_from_decomposition(_decomposition(), _provenance())

        first, second = plan.items
        assert first.id == sid("sub-1")
        assert first.owner == "engineering"
        assert first.estimated_complexity is Complexity.COMPLEX
        assert first.stakes is Stakes.HIGH
        assert second.dependencies == (sid("sub-1"),)
        assert second.owner is None

    def test_status_override(self) -> None:
        plan = plan_from_decomposition(
            _decomposition(), _provenance(status=PlanStatus.DRAFT)
        )
        assert plan.status is PlanStatus.DRAFT

    def test_review_is_attached_when_supplied(self) -> None:
        review = PlanReview(
            verdict=PlanReviewVerdict.CONCERNS,
            reviewers=(
                PlanReviewerVerdict(
                    reviewer_role=NotBlankStr("CTO"),
                    reviewer_id=NotBlankStr("agent-cto"),
                    verdict=PlanReviewVerdict.CONCERNS,
                    findings=(
                        PlanReviewFinding(
                            category=PlanReviewFindingCategory.GAP,
                            detail=NotBlankStr("no rollback"),
                        ),
                    ),
                ),
            ),
            summary=NotBlankStr("1 of 1 reviewer(s) raised concerns"),
            reviewed_at=_CREATED_AT,
        )
        plan = plan_from_decomposition(_decomposition(), _provenance(review=review))
        assert plan.review is not None
        assert plan.review.verdict is PlanReviewVerdict.CONCERNS
        assert plan.review.reviewers[0].reviewer_role == "CTO"

    def test_review_defaults_to_none(self) -> None:
        plan = plan_from_decomposition(_decomposition(), _provenance())
        assert plan.review is None

    def test_open_questions_and_assumptions_carry_from_the_decomposition(self) -> None:
        decomposition = DecompositionResult(
            plan=DecompositionPlan(
                parent_task_id=sid("root"),
                subtasks=(
                    SubtaskDefinition(
                        id=sid("sub-1"),
                        title="Board",
                        description="Grid",
                        acceptance_criteria=(NotBlankStr("board renders"),),
                        expected_artifacts=(NotBlankStr("src/board.py"),),
                    ),
                ),
                open_questions=(NotBlankStr("Which backend?"),),
                assumptions=(NotBlankStr("Single-player only"),),
                task_structure=TaskStructure.SEQUENTIAL,
            ),
            created_tasks=(_result_task("sub-1"),),
        )
        plan = plan_from_decomposition(decomposition, _provenance())
        assert plan.open_questions == ("Which backend?",)
        assert plan.assumptions == ("Single-player only",)

    def test_objective_criteria_are_denormalised_onto_the_plan(self) -> None:
        plan = plan_from_decomposition(
            _decomposition(),
            _provenance(objective_criteria=(NotBlankStr("Playable board"),)),
        )
        assert plan.objective_criteria == ("Playable board",)

    def test_satisfies_projects_from_subtask_to_item(self) -> None:
        decomposition = DecompositionResult(
            plan=DecompositionPlan(
                parent_task_id=sid("root"),
                subtasks=(
                    SubtaskDefinition(
                        id=sid("sub-1"),
                        title="Board",
                        description="Grid",
                        acceptance_criteria=(NotBlankStr("board renders"),),
                        expected_artifacts=(NotBlankStr("src/board.py"),),
                        satisfies=(NotBlankStr("Playable board"),),
                    ),
                ),
                task_structure=TaskStructure.SEQUENTIAL,
            ),
            created_tasks=(_result_task("sub-1"),),
        )
        plan = plan_from_decomposition(decomposition, _provenance())
        assert plan.items[0].satisfies == ("Playable board",)

    def test_artifacts_and_criteria_projected_from_subtask(self) -> None:
        # The subtask-level expected_artifacts + acceptance_criteria must land
        # on the plan item so the durable plan (and every task built from it)
        # arms the fail-loud zero-artifact guard.
        decomposition = DecompositionResult(
            plan=DecompositionPlan(
                parent_task_id=sid("root"),
                subtasks=(
                    SubtaskDefinition(
                        id=sid("sub-1"),
                        title="Board",
                        description="Grid + rendering",
                        expected_artifacts=(
                            NotBlankStr("src/board.tsx"),
                            NotBlankStr("tests/board.test.tsx"),
                        ),
                        acceptance_criteria=(NotBlankStr("renders a 10x20 grid"),),
                    ),
                ),
                task_structure=TaskStructure.SEQUENTIAL,
            ),
            created_tasks=(_result_task("sub-1"),),
        )
        plan = plan_from_decomposition(decomposition, _provenance())
        item = plan.items[0]
        assert item.expected_artifacts == ("src/board.tsx", "tests/board.test.tsx")
        assert item.acceptance_criteria == ("renders a 10x20 grid",)
        # And the reverse projection round-trips them back onto the subtask.
        rebuilt = decomposition_from_plan(plan, parent_task=_parent_task())
        subtask = rebuilt.plan.subtasks[0]
        assert subtask.expected_artifacts == ("src/board.tsx", "tests/board.test.tsx")
        assert subtask.acceptance_criteria == ("renders a 10x20 grid",)


def _parent_task() -> Task:
    return Task(
        id=as_uuid("root"),
        title="Objective",
        description="Ship the game",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="beachhead",
        created_by="ceo",
    )


def _durable_plan() -> Plan:
    return Plan(
        id=as_uuid("plan-1"),
        project=NotBlankStr("beachhead"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the game"),
        parent_task_id=NotBlankStr(str(as_uuid("root"))),
        items=(
            PlanItem(
                id=NotBlankStr(str(as_uuid("sub-1"))),
                title=NotBlankStr("Board"),
                description=NotBlankStr("Grid"),
                required_skills=(NotBlankStr("frontend"),),
                acceptance_criteria=(NotBlankStr("board renders"),),
                expected_artifacts=(NotBlankStr("src/board.py"),),
            ),
            PlanItem(
                id=NotBlankStr(str(as_uuid("sub-2"))),
                title=NotBlankStr("Movement"),
                description=NotBlankStr("Drop"),
                dependencies=(NotBlankStr(str(as_uuid("sub-1"))),),
                owner=NotBlankStr("engineering"),
                acceptance_criteria=(NotBlankStr("pieces move"),),
                expected_artifacts=(NotBlankStr("src/movement.py"),),
            ),
        ),
        task_structure=TaskStructure.PARALLEL,
        coordination_topology=CoordinationTopology.CENTRALIZED,
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


class TestDecompositionFromPlan:
    def test_rebuilds_dispatchable_result(self) -> None:
        result = decomposition_from_plan(_durable_plan(), parent_task=_parent_task())

        assert result.plan.parent_task_id == str(as_uuid("root"))
        assert result.plan.task_structure is TaskStructure.PARALLEL
        assert {s.id for s in result.plan.subtasks} == {
            str(as_uuid("sub-1")),
            str(as_uuid("sub-2")),
        }
        # Child tasks are fresh CREATED work with ids derived from item ids.
        assert {str(t.id) for t in result.created_tasks} == {
            str(as_uuid("sub-1")),
            str(as_uuid("sub-2")),
        }
        assert all(
            str(t.parent_task_id) == str(as_uuid("root")) for t in result.created_tasks
        )
        assert result.dependency_edges == (
            (str(as_uuid("sub-1")), str(as_uuid("sub-2"))),
        )

    def test_decision_items_are_excluded_from_dispatch(self) -> None:
        # A decision item is resolved by the reviewer's choice, not executed, so
        # it never becomes a dispatched task, and a work item's dependency on it
        # is stripped (the decision is made by approval time).
        plan = Plan(
            id=as_uuid("plan-dec"),
            project=NotBlankStr("beachhead"),
            objective_id=NotBlankStr("obj-1"),
            objective_title=NotBlankStr("Ship the game"),
            parent_task_id=NotBlankStr(str(as_uuid("root"))),
            items=(
                PlanItem(
                    id=NotBlankStr(str(as_uuid("decide-stack"))),
                    title=NotBlankStr("Choose the stack"),
                    description=NotBlankStr("React or Svelte"),
                    acceptance_criteria=(NotBlankStr("decision recorded"),),
                    kind=PlanItemKind.DECISION,
                    options=(
                        PlanOption(
                            id=NotBlankStr("react"),
                            title=NotBlankStr("React"),
                            summary=NotBlankStr("Mature, larger bundle"),
                            recommended=True,
                        ),
                        PlanOption(
                            id=NotBlankStr("svelte"),
                            title=NotBlankStr("Svelte"),
                            summary=NotBlankStr("Lean, smaller ecosystem"),
                        ),
                    ),
                ),
                PlanItem(
                    id=NotBlankStr(str(as_uuid("build-ui"))),
                    title=NotBlankStr("Build the UI"),
                    description=NotBlankStr("Render the board"),
                    dependencies=(NotBlankStr(str(as_uuid("decide-stack"))),),
                    acceptance_criteria=(NotBlankStr("board renders"),),
                    expected_artifacts=(NotBlankStr("src/ui.tsx"),),
                ),
            ),
            created_at=_CREATED_AT,
            updated_at=_CREATED_AT,
        )
        result = decomposition_from_plan(plan, parent_task=_parent_task())
        assert {s.id for s in result.plan.subtasks} == {str(as_uuid("build-ui"))}
        assert {str(t.id) for t in result.created_tasks} == {str(as_uuid("build-ui"))}
        # The dependency on the decision item is stripped, so no dangling edge.
        assert result.dependency_edges == ()
        assert result.plan.subtasks[0].dependencies == ()

    def test_routing_hints_survive_round_trip(self) -> None:
        result = decomposition_from_plan(_durable_plan(), parent_task=_parent_task())
        by_id = {s.id: s for s in result.plan.subtasks}
        assert by_id[str(as_uuid("sub-1"))].required_skills == ("frontend",)
        assert by_id[str(as_uuid("sub-2"))].required_role == "engineering"

    def test_satisfies_survives_round_trip(self) -> None:
        plan = Plan(
            id=as_uuid("plan-cov"),
            project=NotBlankStr("beachhead"),
            objective_id=NotBlankStr("obj-1"),
            objective_title=NotBlankStr("Ship the game"),
            parent_task_id=NotBlankStr(str(as_uuid("root"))),
            items=(
                PlanItem(
                    id=NotBlankStr(str(as_uuid("sub-1"))),
                    title=NotBlankStr("Board"),
                    description=NotBlankStr("Grid"),
                    acceptance_criteria=(NotBlankStr("board renders"),),
                    expected_artifacts=(NotBlankStr("src/board.py"),),
                    satisfies=(NotBlankStr("Playable board"),),
                ),
            ),
            created_at=_CREATED_AT,
            updated_at=_CREATED_AT,
        )
        result = decomposition_from_plan(plan, parent_task=_parent_task())
        assert result.plan.subtasks[0].satisfies == ("Playable board",)

    def test_plan_from_decomposition_round_trips(self) -> None:
        # A plan built from a decomposition, then projected back, preserves the
        # item identity, dependency edges, and structure (the mapping's contract).
        plan = plan_from_decomposition(_decomposition(), _provenance())
        rebuilt = decomposition_from_plan(plan, parent_task=_parent_task())
        assert {s.id for s in rebuilt.plan.subtasks} == {item.id for item in plan.items}
        assert rebuilt.plan.task_structure is plan.task_structure
        assert rebuilt.dependency_edges == ((sid("sub-1"), sid("sub-2")),)

    def test_expected_artifacts_and_criteria_reach_the_task(self) -> None:
        # The item's acceptance criteria + expected artifacts must land on the
        # dispatched Task so the fail-loud zero-artifact guard can engage.
        plan = Plan(
            id=as_uuid("plan-2"),
            project=NotBlankStr("beachhead"),
            objective_id=NotBlankStr("obj-1"),
            objective_title=NotBlankStr("Ship the game"),
            parent_task_id=NotBlankStr(str(as_uuid("root"))),
            items=(
                PlanItem(
                    id=NotBlankStr(str(as_uuid("sub-1"))),
                    title=NotBlankStr("Board"),
                    description=NotBlankStr("Grid"),
                    acceptance_criteria=(NotBlankStr("renders an 8x8 grid"),),
                    expected_artifacts=(
                        NotBlankStr("src/board.py"),
                        NotBlankStr("tests/test_board.py"),
                    ),
                ),
            ),
            created_at=_CREATED_AT,
            updated_at=_CREATED_AT,
        )
        result = decomposition_from_plan(plan, parent_task=_parent_task())
        task = result.created_tasks[0]
        assert tuple(c.description for c in task.acceptance_criteria) == (
            "renders an 8x8 grid",
        )
        paths = {a.path for a in task.artifacts_expected}
        assert paths == {"src/board.py", "tests/test_board.py"}
        # The test path is typed TESTS, the source path CODE (inferred).
        by_path = {a.path: a.type for a in task.artifacts_expected}
        assert by_path["tests/test_board.py"] is ArtifactType.TESTS
        assert by_path["src/board.py"] is ArtifactType.CODE


class TestPlanContextReachesTheWork:
    """C11's second half: an answered question reaches the agent, not the row."""

    def test_a_settled_answer_rides_on_every_dispatched_task(self) -> None:
        """The approval writes the answer onto ``assumptions`` and stops there.

        Nothing else on the dispatch path carries a plan-level fact down to
        the work, so an operator answering "Postgres or SQLite?" watched the
        agents pick for themselves.
        """
        plan = _durable_plan().model_copy(
            update={
                "assumptions": (NotBlankStr("Q: Which database? A: Postgres only."),)
            }
        )

        result = decomposition_from_plan(plan, parent_task=_parent_task())

        assert result.created_tasks
        for task in result.created_tasks:
            assert "Q: Which database? A: Postgres only." in task.description
            # Still its own item's work, not replaced by the plan context.
            assert task.title in {"Board", "Movement"}

    def test_an_unanswered_question_is_marked_as_unanswered(self) -> None:
        """An agent must not read an open question as a settled decision."""
        plan = _durable_plan().model_copy(
            update={"open_questions": (NotBlankStr("Do we ship on mobile?"),)}
        )

        result = decomposition_from_plan(plan, parent_task=_parent_task())

        description = result.created_tasks[0].description
        assert "Do we ship on mobile?" in description
        assert "Not decided yet" in description

    def test_a_plan_with_neither_leaves_the_description_alone(self) -> None:
        """The common case adds nothing, so no prompt pays for the mechanism."""
        result = decomposition_from_plan(_durable_plan(), parent_task=_parent_task())

        assert result.created_tasks[0].description == "Grid"
