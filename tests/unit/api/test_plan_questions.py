"""A plan's open questions are answerable, and the answers reach the plan.

The decomposer surfaced questions onto ``plan.open_questions`` and nothing read
that field into any answer surface, so the escalation fired into a void.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.api.lifecycle_helpers.plan_questions import (
    PLAN_ID_METADATA_KEY,
    PLAN_QUESTION_INDEX_KEY,
    apply_plan_question_answer,
    build_plan_questions,
)
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.approval.questions import CLARIFY_ACTION_TYPE, is_question
from synthorg.core.approval import ApprovalItem
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.persistence.plan_protocol import PlanRepository
from tests._shared import FakeClock, as_uuid, mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _plan(*questions: str) -> Plan:
    return Plan(
        id=as_uuid("plan-1"),
        project="proj-1",
        objective_id="obj-1",
        objective_title="Ship the beachhead",
        parent_task_id="task-1",
        items=(
            PlanItem(
                id=str(as_uuid("item-1")),
                title="Board",
                description="Render the grid",
                owner="Backend Developer",
                acceptance_criteria=(NotBlankStr("renders"),),
                expected_artifacts=(NotBlankStr("src/board.py"),),
            ),
        ),
        status=PlanStatus.PENDING_REVIEW,
        open_questions=tuple(NotBlankStr(q) for q in questions),
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestBuildPlanQuestions:
    def test_one_answerable_question_per_open_question(self) -> None:
        plan = _plan("Which datastore?", "Who owns the runbook?")

        parked = build_plan_questions(
            plan,
            task_id=NotBlankStr("task-1"),
            requested_by=NotBlankStr("planner"),
            now=_NOW,
        )

        assert len(parked) == 2
        assert [item.description for item in parked] == [
            "Which datastore?",
            "Who owns the runbook?",
        ]
        assert all(item.action_type == CLARIFY_ACTION_TYPE for item in parked)
        # Listed by the chat surface, which filters on the question action types.
        assert all(is_question(item.action_type) for item in parked)
        assert all(item.status is ApprovalStatus.PENDING for item in parked)
        assert parked[1].metadata[PLAN_ID_METADATA_KEY] == str(plan.id)
        assert parked[1].metadata[PLAN_QUESTION_INDEX_KEY] == "1"
        assert all(item.task_id == "task-1" for item in parked)

    def test_a_plan_with_no_questions_parks_nothing(self) -> None:
        assert (
            build_plan_questions(
                _plan(),
                task_id=NotBlankStr("task-1"),
                requested_by=NotBlankStr("planner"),
                now=_NOW,
            )
            == ()
        )


def _question(plan: Plan, question: str) -> ApprovalItem:
    return build_plan_questions(
        plan,
        task_id=NotBlankStr("task-1"),
        requested_by=NotBlankStr("planner"),
        now=_NOW,
    )[tuple(plan.open_questions).index(NotBlankStr(question))]


class TestApplyPlanQuestionAnswer:
    async def test_an_answer_lands_on_the_plan(self) -> None:
        """The dispatch tree is rebuilt from the plan, so the plan must hear it."""
        plan = _plan("Which datastore?", "Who owns the runbook?")
        repo = mock_of[PlanRepository](
            get=AsyncMock(return_value=plan), update=AsyncMock()
        )

        await apply_plan_question_answer(
            repo,
            _question(plan, "Which datastore?"),
            answer="Postgres, it is already provisioned",
            clock=FakeClock(),
        )

        written: Plan = repo.update.await_args.args[0]
        assert written.open_questions == ("Who owns the runbook?",)
        assert any(
            "Postgres, it is already provisioned" in assumption
            for assumption in written.assumptions
        )
        assert written.version == plan.version + 1

    async def test_a_decline_still_settles_the_question(self) -> None:
        """A declined question is decided, so it stops being listed as open."""
        plan = _plan("Which datastore?")
        repo = mock_of[PlanRepository](
            get=AsyncMock(return_value=plan), update=AsyncMock()
        )

        await apply_plan_question_answer(
            repo, _question(plan, "Which datastore?"), answer=None, clock=FakeClock()
        )

        written: Plan = repo.update.await_args.args[0]
        assert written.open_questions == ()
        assert any("declined to answer" in a for a in written.assumptions)

    async def test_a_question_from_another_surface_is_left_alone(self) -> None:
        """Only a plan question writes back; every other question resumes an agent."""
        repo = mock_of[PlanRepository](get=AsyncMock(), update=AsyncMock())
        item = ApprovalItem(
            action_type=NotBlankStr(CLARIFY_ACTION_TYPE),
            title=NotBlankStr("An agent asked something"),
            description=NotBlankStr("Which colour?"),
            requested_by=NotBlankStr("agent-1"),
            risk_level=ApprovalRiskLevel.MEDIUM,
            created_at=_NOW,
        )

        await apply_plan_question_answer(repo, item, answer="blue", clock=FakeClock())

        repo.get.assert_not_awaited()
        repo.update.assert_not_awaited()

    async def test_an_already_settled_question_is_a_noop(self) -> None:
        """An operator edit may have resolved it first; the decision still stands."""
        parked_from = _plan("Which datastore?")
        item = _question(parked_from, "Which datastore?")
        repo = mock_of[PlanRepository](
            get=AsyncMock(return_value=_plan()), update=AsyncMock()
        )

        await apply_plan_question_answer(
            repo, item, answer="anything", clock=FakeClock()
        )

        repo.update.assert_not_awaited()
