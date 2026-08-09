"""A plan's open questions are answerable, and the answers reach the plan.

The decomposer surfaced questions onto ``plan.open_questions`` and nothing read
that field into any answer surface, so the escalation fired into a void.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.api.lifecycle_helpers.plan_questions import (
    PLAN_ID_METADATA_KEY,
    apply_plan_question_answer,
    build_plan_questions,
    replay_decided_questions,
)
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.approval.questions import CLARIFY_ACTION_TYPE, is_question
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import VersionConflictError
from synthorg.core.persistence_errors import PersistenceVersionConflictError
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
        # The plan id and the question text are the whole key: a position
        # would go stale the moment another question is answered.
        assert set(parked[1].metadata) == {PLAN_ID_METADATA_KEY}
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

    async def test_one_answer_settles_one_of_two_identical_questions(self) -> None:
        """A planner may raise the same question per blocked item.

        Answering one of them settles one. Dropping both would retire a
        question nobody was asked, and the plan would run on an assumption
        recorded once for two decisions.
        """
        plan = _plan("Which datastore?", "Which datastore?")
        repo = mock_of[PlanRepository](
            get=AsyncMock(return_value=plan), update=AsyncMock()
        )

        await apply_plan_question_answer(
            repo,
            _question(plan, "Which datastore?"),
            answer="Postgres",
            clock=FakeClock(),
        )

        written: Plan = repo.update.await_args.args[0]
        assert written.open_questions == ("Which datastore?",)

    async def test_a_concurrent_answer_is_retried_rather_than_reported_failed(
        self,
    ) -> None:
        """Two answers on one plan contend; the second is not wrong, just second.

        The plan row carries every question, so answering two at once makes
        one writer lose the version check. Reporting that as a failed decision
        would tell the operator their answer did not land while the other one
        did, so the loser re-reads and re-applies.
        """
        first = _plan("Which datastore?", "Who owns the runbook?")
        # The winner's write already removed the other question and bumped
        # the version, which is exactly the row the loser must re-read.
        after_other = first.model_copy(
            update={
                "open_questions": (NotBlankStr("Which datastore?"),),
                "version": first.version + 1,
            }
        )
        repo = mock_of[PlanRepository](
            get=AsyncMock(side_effect=[first, after_other]),
            update=AsyncMock(
                side_effect=[PersistenceVersionConflictError("moved"), None]
            ),
        )

        await apply_plan_question_answer(
            repo,
            _question(first, "Which datastore?"),
            answer="Postgres",
            clock=FakeClock(),
        )

        assert repo.update.await_count == 2
        written: Plan = repo.update.await_args.args[0]
        assert written.open_questions == ()
        assert written.version == after_other.version + 1

    async def test_an_answer_that_never_lands_is_reported(self) -> None:
        """Losing every attempt is a decision that did not reach the plan."""
        plan = _plan("Which datastore?")
        repo = mock_of[PlanRepository](
            get=AsyncMock(return_value=plan),
            update=AsyncMock(side_effect=PersistenceVersionConflictError("moved")),
        )

        with pytest.raises(VersionConflictError):
            await apply_plan_question_answer(
                repo,
                _question(plan, "Which datastore?"),
                answer="Postgres",
                clock=FakeClock(),
            )


def _decided(item: ApprovalItem, *, answer: str | None) -> ApprovalItem:
    return item.model_copy(
        update={
            "status": (ApprovalStatus.APPROVED if answer else ApprovalStatus.REJECTED),
            "decision_reason": answer or "The operator declined to answer.",
            "decided_at": _NOW,
        }
    )


class TestReplayDecidedQuestions:
    """A decision that did not reach the plan is replayed before dispatch.

    The write-back after a decision runs once the decision is already durable
    and degrades on a persistence failure, so the plan can reach dispatch
    still asking something the operator answered. The approvals carry the
    decisions, so they are the record the plan is brought back to.
    """

    async def test_a_decided_answer_still_listed_open_is_replayed(self) -> None:
        plan = _plan("Which datastore?")
        settled = _plan()
        repo = mock_of[PlanRepository](
            get=AsyncMock(side_effect=[plan, settled]), update=AsyncMock()
        )
        store = mock_of[ApprovalStoreProtocol](
            list_items=AsyncMock(
                return_value=(
                    _decided(_question(plan, "Which datastore?"), answer="Postgres"),
                )
            )
        )

        result = await replay_decided_questions(repo, store, plan, clock=FakeClock())

        written: Plan = repo.update.await_args.args[0]
        assert written.open_questions == ()
        assert any("Postgres" in a for a in written.assumptions)
        assert result is settled

    async def test_a_decline_is_replayed_as_a_decline(self) -> None:
        plan = _plan("Which datastore?")
        repo = mock_of[PlanRepository](
            get=AsyncMock(side_effect=[plan, _plan()]), update=AsyncMock()
        )
        store = mock_of[ApprovalStoreProtocol](
            list_items=AsyncMock(
                return_value=(
                    _decided(_question(plan, "Which datastore?"), answer=None),
                )
            )
        )

        await replay_decided_questions(repo, store, plan, clock=FakeClock())

        written: Plan = repo.update.await_args.args[0]
        assert any("declined to answer" in a for a in written.assumptions)

    async def test_a_pending_question_is_left_open(self) -> None:
        """Nobody has decided it, so there is nothing to replay."""
        plan = _plan("Which datastore?")
        repo = mock_of[PlanRepository](get=AsyncMock(), update=AsyncMock())
        store = mock_of[ApprovalStoreProtocol](
            list_items=AsyncMock(return_value=(_question(plan, "Which datastore?"),))
        )

        assert (
            await replay_decided_questions(repo, store, plan, clock=FakeClock()) is plan
        )
        repo.update.assert_not_awaited()

    async def test_an_expired_question_is_not_replayed_as_a_decline(self) -> None:
        """Expiry is nobody's decision; inventing one puts words in a mouth."""
        plan = _plan("Which datastore?")
        expired = _question(plan, "Which datastore?").model_copy(
            update={"status": ApprovalStatus.EXPIRED}
        )
        repo = mock_of[PlanRepository](get=AsyncMock(), update=AsyncMock())
        store = mock_of[ApprovalStoreProtocol](
            list_items=AsyncMock(return_value=(expired,))
        )

        await replay_decided_questions(repo, store, plan, clock=FakeClock())

        repo.update.assert_not_awaited()

    async def test_a_decision_on_another_plan_is_not_replayed(self) -> None:
        plan = _plan("Which datastore?")
        other = _decided(
            _question(plan, "Which datastore?"), answer="Postgres"
        ).model_copy(update={"metadata": {PLAN_ID_METADATA_KEY: "some-other-plan"}})
        repo = mock_of[PlanRepository](get=AsyncMock(), update=AsyncMock())
        store = mock_of[ApprovalStoreProtocol](
            list_items=AsyncMock(return_value=(other,))
        )

        await replay_decided_questions(repo, store, plan, clock=FakeClock())

        repo.update.assert_not_awaited()

    async def test_a_plan_with_nothing_open_reads_no_approvals(self) -> None:
        """The scan is skipped entirely when there is nothing left to settle."""
        plan = _plan()
        repo = mock_of[PlanRepository](get=AsyncMock(), update=AsyncMock())
        store = mock_of[ApprovalStoreProtocol](list_items=AsyncMock())

        assert (
            await replay_decided_questions(repo, store, plan, clock=FakeClock()) is plan
        )
        store.list_items.assert_not_awaited()

    async def test_no_store_leaves_the_plan_as_it_stands(self) -> None:
        plan = _plan("Which datastore?")
        repo = mock_of[PlanRepository](get=AsyncMock(), update=AsyncMock())

        assert (
            await replay_decided_questions(repo, None, plan, clock=FakeClock()) is plan
        )
        repo.update.assert_not_awaited()

    async def test_an_identical_question_still_pending_holds_its_occurrence(
        self,
    ) -> None:
        """The second approval must not be settled by the first one's answer.

        A planner may raise the same question once per item it blocks, so the
        text does not identify the occurrence. One decided approval settles
        one occurrence; the other stays open until its own approval is
        decided, or it would vanish without ever being answered.
        """
        # The first answer already landed, so one occurrence remains open and
        # one approval is still PENDING: they match, and nothing is owed.
        plan = _plan("Which datastore?")
        both = build_plan_questions(
            _plan("Which datastore?", "Which datastore?"),
            task_id=NotBlankStr("task-1"),
            requested_by=NotBlankStr("planner"),
            now=_NOW,
        )
        repo = mock_of[PlanRepository](get=AsyncMock(), update=AsyncMock())
        store = mock_of[ApprovalStoreProtocol](
            list_items=AsyncMock(
                return_value=(_decided(both[0], answer="Postgres"), both[1])
            )
        )

        assert (
            await replay_decided_questions(repo, store, plan, clock=FakeClock()) is plan
        )
        repo.update.assert_not_awaited()

    async def test_replaying_the_same_decision_twice_settles_one_occurrence(
        self,
    ) -> None:
        """A second pass is a no-op, so a retry cannot eat a second question."""
        two_open = _plan("Which datastore?", "Which datastore?")
        one_open = _plan("Which datastore?")
        both = build_plan_questions(
            two_open,
            task_id=NotBlankStr("task-1"),
            requested_by=NotBlankStr("planner"),
            now=_NOW,
        )
        decided = _decided(both[0], answer="Postgres")
        repo = mock_of[PlanRepository](
            get=AsyncMock(side_effect=[two_open, one_open]), update=AsyncMock()
        )
        store = mock_of[ApprovalStoreProtocol](
            list_items=AsyncMock(return_value=(decided, both[1]))
        )

        first = await replay_decided_questions(repo, store, two_open, clock=FakeClock())

        assert first.open_questions == ("Which datastore?",)
        assert repo.update.await_count == 1

        # Same store, same decision, the plan as the first pass left it.
        assert (
            await replay_decided_questions(repo, store, first, clock=FakeClock())
            is first
        )
        assert repo.update.await_count == 1

    async def test_more_pending_approvals_than_occurrences_replays_nothing(
        self,
    ) -> None:
        """An operator edit can drop an occurrence its approval still awaits.

        Fewer occurrences than approvals means the plan owes nothing, so the
        surplus is negative and must read as zero: taken as a slice bound it
        would replay every decided approval but the last.
        """
        plan = _plan("Which datastore?")
        three = build_plan_questions(
            _plan("Which datastore?", "Which datastore?", "Which datastore?"),
            task_id=NotBlankStr("task-1"),
            requested_by=NotBlankStr("planner"),
            now=_NOW,
        )
        repo = mock_of[PlanRepository](get=AsyncMock(), update=AsyncMock())
        store = mock_of[ApprovalStoreProtocol](
            list_items=AsyncMock(
                return_value=(
                    _decided(three[0], answer="Postgres"),
                    three[1],
                    three[2],
                )
            )
        )

        assert (
            await replay_decided_questions(repo, store, plan, clock=FakeClock()) is plan
        )
        repo.update.assert_not_awaited()

    async def test_both_decided_settles_both_occurrences(self) -> None:
        """Two answers owed, two occurrences settled, and the plan is clear."""
        two_open = _plan("Which datastore?", "Which datastore?")
        one_open = _plan("Which datastore?")
        settled = _plan()
        both = build_plan_questions(
            two_open,
            task_id=NotBlankStr("task-1"),
            requested_by=NotBlankStr("planner"),
            now=_NOW,
        )
        repo = mock_of[PlanRepository](
            get=AsyncMock(side_effect=[two_open, one_open, settled]),
            update=AsyncMock(),
        )
        store = mock_of[ApprovalStoreProtocol](
            list_items=AsyncMock(
                return_value=(
                    _decided(both[0], answer="Postgres"),
                    _decided(both[1], answer="Postgres"),
                )
            )
        )

        result = await replay_decided_questions(
            repo, store, two_open, clock=FakeClock()
        )

        assert repo.update.await_count == 2
        assert result is settled
