"""Tests for the re-planning pass an operator's change request drives.

The invariant: "send this back for revision" produces a revised plan, or is
refused. What it must never do is what shipped: flip the plan to draft, record
the note on an audit event, and leave it there with nothing on any surface that
would ever revise it. A live run left a plan parked exactly that way, carrying
assumptions that named files nobody had written.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers._plan_rework import replan_for_change_request
from synthorg.core.domain_errors import (
    ConflictError,
    ServiceUnavailableError,
    ValidationError,
)
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import (
    PlanReviewFindingCategory,
    PlanReviewVerdict,
    PlanStatus,
)
from synthorg.core.plan_review import (
    PlanReview,
    PlanReviewerVerdict,
    PlanReviewFinding,
)
from synthorg.core.project import Project
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStructure, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.state import EngineStateSlice
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.state import HrStateSlice
from synthorg.persistence.state import persistence_of
from synthorg.workers.state import RuntimeStateSlice
from tests._shared import LoopAsyncClient, as_uuid, sid
from tests._shared.scripted_provider import make_e2e_identity

pytestmark = pytest.mark.unit

_AT = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)
_OBJECTIVE = "Ship the falling-blocks game"


def _objective_task() -> Task:
    """The objective task a plan decomposes."""
    return Task(
        id=as_uuid("objective-1"),
        title="Objective",
        description=_OBJECTIVE,
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="operator-1",
    )


def _plan(*, review: PlanReview | None = None) -> Plan:
    """A plan under review, optionally carrying a panel verdict."""
    return Plan(
        id=as_uuid("plan-rework"),
        project=sid("proj-1"),
        project_name=NotBlankStr("Blocks"),
        objective_id=NotBlankStr("charter-1"),
        objective_title=NotBlankStr("Ship the game"),
        parent_task_id=sid("objective-1"),
        items=(
            PlanItem(
                id=sid("item-1"),
                title=NotBlankStr("Slice"),
                description=NotBlankStr("Deliver the slice"),
                acceptance_criteria=(NotBlankStr("done"),),
                expected_artifacts=(NotBlankStr("src/slice.py"),),
            ),
        ),
        task_structure=TaskStructure.SEQUENTIAL,
        status=PlanStatus.PENDING_REVIEW,
        review=review,
        created_at=_AT,
        updated_at=_AT,
    )


def _review(detail: str) -> PlanReview:
    """A panel verdict raising one finding."""
    return PlanReview(
        verdict=PlanReviewVerdict.CONCERNS,
        reviewers=(
            PlanReviewerVerdict(
                reviewer_role=NotBlankStr("CTO"),
                reviewer_id=NotBlankStr("agent-1"),
                verdict=PlanReviewVerdict.CONCERNS,
                findings=(
                    PlanReviewFinding(
                        category=PlanReviewFindingCategory.GAP,
                        detail=NotBlankStr(detail),
                    ),
                ),
            ),
        ),
        reviewed_at=_AT,
    )


def _decomposition_result() -> DecompositionResult:
    """A minimal real decomposition the planner hands back."""
    plan = DecompositionPlan(
        parent_task_id=sid("objective-1"),
        subtasks=(
            SubtaskDefinition(
                id=sid("revised-1"),
                title="Build the engine",
                description="Author the falling-block engine",
                acceptance_criteria=("pieces fall and lock",),
                expected_artifacts=("src/engine.py",),
            ),
        ),
        task_structure=TaskStructure.SEQUENTIAL,
        assumptions=("the workspace starts empty",),
        open_questions=("which host?",),
    )
    return DecompositionResult(
        plan=plan,
        created_tasks=(
            Task(
                id=as_uuid("revised-1"),
                title="Build the engine",
                description="Author the falling-block engine",
                type=TaskType.DEVELOPMENT,
                priority=Priority.MEDIUM,
                project="proj-1",
                created_by="operator-1",
            ),
        ),
    )


@pytest.fixture
def planner(async_test_client: LoopAsyncClient) -> SimpleNamespace:
    """Wire a scripted planner and a task engine holding the objective."""
    app_state = async_test_client.app.state.app_state
    decomposition = AsyncMock()
    decomposition.decompose_task.return_value = _decomposition_result()
    app_state.wire(
        RuntimeStateSlice,
        coordinator=SimpleNamespace(decomposition_service=decomposition),
    )
    engine = AsyncMock()
    engine.get_task.return_value = _objective_task()
    app_state.wire(EngineStateSlice, task_engine=engine)
    # A real registry: owner validation refuses to run without one, the same
    # guard every path handing the plan service new items passes through.
    app_state.wire(HrStateSlice, agent_registry=AgentRegistryService())
    return SimpleNamespace(
        app_state=app_state, decomposition=decomposition, engine=engine
    )


class TestTheNoteReachesThePlanner:
    async def test_a_change_request_re_plans(self, planner: SimpleNamespace) -> None:
        replanned = await replan_for_change_request(
            planner.app_state, _plan(), note="split movement into drop and rotate"
        )
        assert len(replanned.items) == 1
        assert replanned.items[0].title == "Build the engine"

    async def test_the_planner_is_briefed_with_the_operators_words(
        self, planner: SimpleNamespace
    ) -> None:
        """The note is the whole point; a pass that drops it re-plans nothing."""
        await replan_for_change_request(
            planner.app_state, _plan(), note="split movement into drop and rotate"
        )
        briefed = planner.decomposition.decompose_task.await_args.args[0]
        assert "split movement into drop and rotate" in briefed.description

    async def test_the_brief_rides_on_a_copy_never_the_objective(
        self, planner: SimpleNamespace
    ) -> None:
        """The objective did not change; only this pass sees the brief."""
        await replan_for_change_request(
            planner.app_state, _plan(), note="split movement"
        )
        briefed = planner.decomposition.decompose_task.await_args.args[0]
        assert briefed.description.startswith(_OBJECTIVE)
        assert briefed.id == _objective_task().id

    async def test_outstanding_panel_findings_ride_along(
        self, planner: SimpleNamespace
    ) -> None:
        """The operator is overriding the panel, not discarding it."""
        await replan_for_change_request(
            planner.app_state,
            _plan(review=_review("no item builds the engine")),
            note="split movement",
        )
        briefed = planner.decomposition.decompose_task.await_args.args[0]
        assert "no item builds the engine" in briefed.description

    async def test_a_bare_note_less_request_still_re_plans_the_findings(
        self, planner: SimpleNamespace
    ) -> None:
        """ "Send it back" with findings outstanding is a complete instruction."""
        replanned = await replan_for_change_request(
            planner.app_state,
            _plan(review=_review("no item builds the engine")),
            note=None,
        )
        assert len(replanned.items) == 1


class TestThePremisesTravelWithTheItems:
    async def test_the_re_planned_assumptions_come_back(
        self, planner: SimpleNamespace
    ) -> None:
        """A live run replaced every item with "build it from scratch" while
        the plan went on asserting the thing already existed, because the
        rework carried the superseded plan's premises forward.
        """
        replanned = await replan_for_change_request(
            planner.app_state, _plan(), note="nothing exists yet"
        )
        assert replanned.premises.assumptions == ("the workspace starts empty",)

    async def test_the_re_planned_open_questions_come_back(
        self, planner: SimpleNamespace
    ) -> None:
        """Stale questions outlive the plan that could not answer them."""
        replanned = await replan_for_change_request(
            planner.app_state, _plan(), note="nothing exists yet"
        )
        assert replanned.premises.open_questions == ("which host?",)


class TestItPlansAsTheInitiativesOwner:
    async def test_the_pass_is_owned_by_the_projects_lead(
        self, planner: SimpleNamespace, async_test_client: LoopAsyncClient
    ) -> None:
        """Without an identity the agent-session strategy declines.

        It falls back to the single-shot decomposer, so a rework would be
        planned by a different mechanism than the plan it revises. A live
        rework fell back exactly that way and exhausted its parse retries.
        """
        lead = make_e2e_identity(label="lead-agent")
        registry = AgentRegistryService()
        await registry.register(lead)
        planner.app_state.wire(HrStateSlice, agent_registry=registry)
        backend = persistence_of(async_test_client.app.state.app_state)
        await backend.projects.save(
            Project(
                id=as_uuid("proj-1"),
                name=NotBlankStr("Blocks"),
                description=NotBlankStr("The falling-blocks initiative"),
                lead=NotBlankStr(str(lead.id)),
            )
        )

        await replan_for_change_request(
            planner.app_state, _plan(), note="split movement"
        )

        context = planner.decomposition.decompose_task.await_args.args[1]
        assert context.owner_identity is not None
        assert context.owner_identity.id == lead.id

    async def test_an_unresolvable_lead_still_plans(
        self, planner: SimpleNamespace
    ) -> None:
        """Degrading is worse than planning as the owner, but it still plans.

        Refusing an operator's change request over a missing lead would be a
        worse answer than the fallback decomposer.
        """
        replanned = await replan_for_change_request(
            planner.app_state, _plan(), note="split movement"
        )
        context = planner.decomposition.decompose_task.await_args.args[1]
        assert context.owner_identity is None
        assert len(replanned.items) == 1


class TestItRefusesRatherThanParks:
    async def test_no_planner_is_refused_loudly(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """Parking a plan for a revision nobody performs is the defect."""
        app_state = async_test_client.app.state.app_state
        app_state.wire(RuntimeStateSlice, coordinator=None)
        with pytest.raises(ServiceUnavailableError, match="No planner"):
            await replan_for_change_request(app_state, _plan(), note="revise")

    async def test_nothing_to_revise_is_refused_before_any_spend(
        self, planner: SimpleNamespace
    ) -> None:
        """No note and no findings would re-plan against nothing."""
        with pytest.raises(ValidationError, match="Say what should change"):
            await replan_for_change_request(planner.app_state, _plan(), note=None)
        planner.decomposition.decompose_task.assert_not_awaited()

    async def test_a_blank_note_counts_as_no_note(
        self, planner: SimpleNamespace
    ) -> None:
        with pytest.raises(ValidationError, match="Say what should change"):
            await replan_for_change_request(planner.app_state, _plan(), note="   ")

    async def test_a_terminal_plan_is_refused(self, planner: SimpleNamespace) -> None:
        decided = _plan().model_copy(update={"status": PlanStatus.REJECTED})
        with pytest.raises(ConflictError):
            await replan_for_change_request(planner.app_state, decided, note="revise")

    async def test_a_missing_objective_task_is_refused(
        self, planner: SimpleNamespace
    ) -> None:
        """Without the objective there is nothing left to plan against."""
        planner.engine.get_task.return_value = None
        with pytest.raises(ConflictError, match="no longer exists"):
            await replan_for_change_request(planner.app_state, _plan(), note="revise")
