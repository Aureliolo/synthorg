"""Tests for the bounded re-plan loop a panel verdict drives.

The invariant: a finding the panel raised must reach the planner, and must do
so a bounded number of times. Before this loop existed, findings were
synthesised, persisted, rendered and then ignored, so the operator was handed
a plan carrying every objection the panel had raised against it.

These assert the invariant rather than the run that exposed it: what matters is
that a demand for revision produces another planning pass carrying the demand,
that the pass is capped, and that the review the operator finally sees belongs
to the plan they are finally shown.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.plan_enums import PlanReviewFindingCategory, PlanReviewVerdict
from synthorg.core.plan_review import (
    PlanReview,
    PlanReviewerVerdict,
    PlanReviewFinding,
    PlanReviewOutcome,
)
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStructure, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.pipeline.plan_revision import build_reviewed_plan
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_AT = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)
_OBJECTIVE = "Ship the falling-blocks game"


def _task() -> Task:
    """The objective task the loop plans and re-plans."""
    return Task(
        id=as_uuid("task-1"),
        title="Objective",
        description=_OBJECTIVE,
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="operator-1",
    )


def _plan(slug: str) -> DecompositionResult:
    """A minimal real decomposition, distinguishable by *slug*."""
    plan = DecompositionPlan(
        parent_task_id=sid("task-1"),
        subtasks=(
            SubtaskDefinition(
                id=sid(slug),
                title=f"Slice {slug}",
                description="Deliver the slice",
                expected_artifacts=(f"src/{slug}.py",),
            ),
        ),
        task_structure=TaskStructure.SEQUENTIAL,
    )
    return DecompositionResult(
        plan=plan,
        created_tasks=(
            Task(
                id=as_uuid(slug),
                title=f"Slice {slug}",
                description="Deliver the slice",
                type=TaskType.DEVELOPMENT,
                priority=Priority.MEDIUM,
                project="proj-1",
                created_by="operator-1",
            ),
        ),
    )


def _objecting(detail: str) -> PlanReviewOutcome:
    """A review that demands revision, itemising *detail*."""
    return PlanReviewOutcome(
        review=PlanReview(
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
    )


def _content() -> PlanReviewOutcome:
    """A review that demands nothing."""
    return PlanReviewOutcome(
        review=PlanReview(
            verdict=PlanReviewVerdict.ENDORSED,
            reviewers=(
                PlanReviewerVerdict(
                    reviewer_role=NotBlankStr("CTO"),
                    reviewer_id=NotBlankStr("agent-1"),
                    verdict=PlanReviewVerdict.ENDORSED,
                    findings=(),
                ),
            ),
            reviewed_at=_AT,
        )
    )


def _no_panel() -> PlanReviewOutcome:
    """The outcome a pipeline with no panel attached produces."""
    return PlanReviewOutcome(absent_reason=NotBlankStr("no panel attached"))


class _Recorder:
    """Drives the loop and records what each side was asked for."""

    def __init__(self, *outcomes: PlanReviewOutcome) -> None:
        """Answer each review round from *outcomes*, holding the last."""
        self._outcomes = outcomes
        self.briefs: list[str] = []
        self.rounds_reviewed: list[int] = []

    async def build_plan(self, planned: Task) -> DecompositionResult:
        """Record the description planned against, and return a fresh plan."""
        self.briefs.append(planned.description)
        return _plan(f"sub-{len(self.briefs)}")

    async def review_plan(
        self,
        round_index: int,
        planned: Task,
        plan: DecompositionResult,
    ) -> PlanReviewOutcome:
        """Return the scripted outcome for this round."""
        del planned, plan
        self.rounds_reviewed.append(round_index)
        index = min(len(self.rounds_reviewed) - 1, len(self._outcomes) - 1)
        return self._outcomes[index]


class TestSettledPlans:
    async def test_a_plan_nobody_objects_to_is_planned_once(self) -> None:
        recorder = _Recorder(_content())
        result = await build_reviewed_plan(
            task=_task(),
            build_plan=recorder.build_plan,
            review_plan=recorder.review_plan,
            max_rounds=2,
        )
        assert result.rounds_used == 0
        assert result.settled
        assert recorder.briefs == [_OBJECTIVE]

    async def test_no_panel_never_re_plans(self) -> None:
        """An unwired panel is not an objection; looping would burn rounds."""
        recorder = _Recorder(_no_panel())
        result = await build_reviewed_plan(
            task=_task(),
            build_plan=recorder.build_plan,
            review_plan=recorder.review_plan,
            max_rounds=3,
        )
        assert result.rounds_used == 0
        assert result.settled


class TestFindingsReachThePlanner:
    async def test_a_finding_drives_another_planning_pass(self) -> None:
        """The defect this loop exists for: 18 findings reached nobody."""
        recorder = _Recorder(_objecting("no item builds the engine"), _content())
        result = await build_reviewed_plan(
            task=_task(),
            build_plan=recorder.build_plan,
            review_plan=recorder.review_plan,
            max_rounds=2,
        )
        assert result.rounds_used == 1
        assert result.settled
        assert len(recorder.briefs) == 2

    async def test_the_second_pass_carries_the_finding(self) -> None:
        recorder = _Recorder(_objecting("no item builds the engine"), _content())
        await build_reviewed_plan(
            task=_task(),
            build_plan=recorder.build_plan,
            review_plan=recorder.review_plan,
            max_rounds=2,
        )
        assert "no item builds the engine" in recorder.briefs[1]

    async def test_every_pass_briefs_off_the_original_objective(self) -> None:
        """A superseded round's findings describe a plan that no longer exists.

        Accumulating them asks the planner to fix a plan it already replaced,
        and grows the prompt without bound.
        """
        recorder = _Recorder(
            _objecting("first objection"),
            _objecting("second objection"),
            _content(),
        )
        await build_reviewed_plan(
            task=_task(),
            build_plan=recorder.build_plan,
            review_plan=recorder.review_plan,
            max_rounds=3,
        )
        assert "second objection" in recorder.briefs[2]
        assert "first objection" not in recorder.briefs[2]
        assert recorder.briefs[2].startswith(_OBJECTIVE)

    async def test_each_round_reviews_under_its_own_index(self) -> None:
        """The caller records a phase per round; a shared index hides rounds."""
        recorder = _Recorder(_objecting("keeps objecting"))
        await build_reviewed_plan(
            task=_task(),
            build_plan=recorder.build_plan,
            review_plan=recorder.review_plan,
            max_rounds=2,
        )
        assert recorder.rounds_reviewed == [0, 1, 2]


class TestTheCapBinds:
    async def test_a_panel_that_never_settles_stops_at_the_cap(self) -> None:
        """A planner and a panel that disagree would disagree indefinitely."""
        recorder = _Recorder(_objecting("keeps objecting"))
        result = await build_reviewed_plan(
            task=_task(),
            build_plan=recorder.build_plan,
            review_plan=recorder.review_plan,
            max_rounds=2,
        )
        assert result.rounds_used == 2
        assert not result.settled

    async def test_exhaustion_still_parks_a_plan(self) -> None:
        """Reaching the cap is not a failure; it is what shipped before."""
        recorder = _Recorder(_objecting("keeps objecting"))
        result = await build_reviewed_plan(
            task=_task(),
            build_plan=recorder.build_plan,
            review_plan=recorder.review_plan,
            max_rounds=1,
        )
        assert result.plan is not None
        assert result.outcome.review is not None

    async def test_a_zero_cap_makes_the_panel_advisory(self) -> None:
        """The operator's explicit opt-out: reviewed, recorded, not acted on."""
        recorder = _Recorder(_objecting("objection nobody acts on"))
        result = await build_reviewed_plan(
            task=_task(),
            build_plan=recorder.build_plan,
            review_plan=recorder.review_plan,
            max_rounds=0,
        )
        assert result.rounds_used == 0
        assert not result.settled
        assert recorder.briefs == [_OBJECTIVE]


class TestTheParkedPlanMatchesItsReview:
    async def test_the_outcome_belongs_to_the_final_plan(self) -> None:
        """Parking round 1's review beside round 2's plan would misreport it."""
        recorder = _Recorder(_objecting("first objection"), _content())
        result = await build_reviewed_plan(
            task=_task(),
            build_plan=recorder.build_plan,
            review_plan=recorder.review_plan,
            max_rounds=2,
        )
        assert result.plan.plan.subtasks[0].id == sid("sub-2")
        assert result.outcome.review is not None
        assert result.outcome.review.verdict is PlanReviewVerdict.ENDORSED
