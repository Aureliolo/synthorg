"""Tests for the agent-session stakeholder plan-review panel."""

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.plan_enums import PlanReviewFindingCategory, PlanReviewVerdict
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
from synthorg.engine.errors import PlanReviewUnavailableError
from synthorg.engine.plan_review.models import PlanReviewPanelConfig
from synthorg.engine.plan_review.session import (
    AgentSessionPlanReviewPanel,
    _review_brief,
)
from tests._shared import FakeClock, as_uuid, sid
from tests._shared.scripted_provider import (
    ScriptedProvider,
    build_tool_call_response,
    make_e2e_identity,
    make_text_response,
)

pytestmark = pytest.mark.unit


def _task() -> Task:
    return Task(
        id=as_uuid("obj-1"),
        title="Ship the beachhead",
        description="Deliver the first slice.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project="beachhead",
        created_by="ceo",
    )


def _plan() -> DecompositionResult:
    plan = DecompositionPlan(
        parent_task_id=sid("root"),
        subtasks=(
            SubtaskDefinition(
                id=sid("item-1"),
                title="Board",
                description="Render the grid",
                estimated_complexity=Complexity.MEDIUM,
                stakes=Stakes.NORMAL,
                required_role="Backend Developer",
                acceptance_criteria=(NotBlankStr("renders"),),
                expected_artifacts=(NotBlankStr("src/board.py"),),
            ),
        ),
        task_structure=TaskStructure.PARALLEL,
        coordination_topology=CoordinationTopology.AUTO,
    )
    return DecompositionResult(
        plan=plan,
        created_tasks=(
            Task(
                id=as_uuid("item-1"),
                title="Board",
                description="Render the grid",
                type=TaskType.DEVELOPMENT,
                priority=Priority.MEDIUM,
                project="beachhead",
                created_by="ceo",
            ),
        ),
    )


def _agent(label: str, *, role: str) -> AgentIdentity:
    return make_e2e_identity(label=label).model_copy(
        update={"role": role, "department": "Executive"}
    )


def _panel(provider: ScriptedProvider) -> AgentSessionPlanReviewPanel:
    return AgentSessionPlanReviewPanel(
        provider_selector=lambda _identity: provider,
        config=PlanReviewPanelConfig(panel_size=1, max_turns=3),
        clock=FakeClock(),
    )


class TestAgentSessionPlanReviewPanel:
    async def test_consolidates_a_submitted_verdict(self) -> None:
        provider = ScriptedProvider(
            [
                build_tool_call_response(
                    "submit_plan_review",
                    {
                        "verdict": "concerns",
                        "findings": [
                            {"category": "budget_concern", "detail": "over budget"}
                        ],
                    },
                ),
                make_text_response("Reviewed."),
            ]
        )
        panel = _panel(provider)
        reviewer = _agent("cfo", role="CFO")

        outcome = await panel.review(
            task=_task(), plan=_plan(), agents=(reviewer,), owner=None
        )

        review = outcome.review
        assert review is not None
        assert outcome.absent_reason is None
        assert review.verdict is PlanReviewVerdict.CONCERNS
        assert len(review.reviewers) == 1
        assert review.reviewers[0].reviewer_role == "CFO"
        assert review.reviewers[0].findings[0].detail == "over budget"

    async def test_a_sequencing_finding_survives_the_panel(self) -> None:
        """The run-1 plan shape lands on its own kind, not on OTHER.

        The brief asks whether real work is parallelised or a needless
        sequential chain, so a reviewer answering that question needs a
        category for it; without one the claim's kind is discarded and the
        operator reads it as unclassified.
        """
        provider = ScriptedProvider(
            [
                build_tool_call_response(
                    "submit_plan_review",
                    {
                        "verdict": "revision_requested",
                        "findings": [
                            {
                                "category": "sequencing",
                                "detail": (
                                    "the integration item names three items it"
                                    " declares no dependency on"
                                ),
                            }
                        ],
                    },
                ),
                make_text_response("Reviewed."),
            ]
        )
        panel = _panel(provider)
        reviewer = _agent("cto", role="CTO")

        outcome = await panel.review(
            task=_task(), plan=_plan(), agents=(reviewer,), owner=None
        )

        review = outcome.review
        assert review is not None
        assert (
            review.reviewers[0].findings[0].category
            is PlanReviewFindingCategory.SEQUENCING
        )

    def test_the_brief_enumerates_every_category(self) -> None:
        """A reviewer invents a category when it was never shown the list.

        The tool schema carries the enum, but the brief is the prose that
        decides what the reviewer looks for, and it asked questions three of
        the categories could not answer. Generated from the enum rather than
        hand-listed, so a new member cannot ship without the prose following.
        """
        brief = _review_brief(_agent("cto", role="CTO"), _task(), "Plan: ...")
        for category in PlanReviewFindingCategory:
            assert category.value in brief

    async def test_no_panel_seated_says_so(self) -> None:
        """An un-panelled plan carries the reason, not a blank review."""
        provider = ScriptedProvider([])
        panel = _panel(provider)
        owner = _agent("owner", role="CTO")

        outcome = await panel.review(
            task=_task(), plan=_plan(), agents=(owner,), owner=owner
        )

        assert outcome.review is None
        assert outcome.absent_reason is not None
        assert "no eligible reviewer" in outcome.absent_reason
        assert provider.call_count == 0

    async def test_no_verdict_submitted_says_so(self) -> None:
        """Corrected once, still no verdict: no review, and a reason.

        The plan still parks (a greenlight is never blocked on the panel), but
        the operator is told it carries zero quality signal rather than shown
        an empty review section that reads like approval.
        """
        provider = ScriptedProvider(
            [
                make_text_response("still thinking"),
                make_text_response("still thinking, honestly"),
            ]
        )
        panel = _panel(provider)
        reviewer = _agent("cto", role="CTO")

        outcome = await panel.review(
            task=_task(), plan=_plan(), agents=(reviewer,), owner=None
        )

        assert outcome.review is None
        assert outcome.absent_reason is not None
        assert "no reviewer submitted a verdict" in outcome.absent_reason
        assert provider.call_count == 2, "the panellist is corrected exactly once"

    async def test_a_prose_answer_is_corrected_into_a_verdict(self) -> None:
        """A reviewer that answered in prose gets one push-back, not a shrug.

        A session that never called its only tool, recorded as an absent
        opinion instead of corrected, sends the plan to its human gate with
        no quality signal, and every panellist fails that way at once.
        """
        provider = ScriptedProvider(
            [
                make_text_response("The plan looks broadly sensible to me."),
                build_tool_call_response(
                    "submit_plan_review",
                    {
                        "verdict": "concerns",
                        "findings": [
                            {"category": "gap", "detail": "item 3 is two items"}
                        ],
                    },
                ),
                make_text_response("Submitted."),
            ]
        )
        panel = _panel(provider)
        reviewer = _agent("cto", role="CTO")

        outcome = await panel.review(
            task=_task(), plan=_plan(), agents=(reviewer,), owner=None
        )

        review = outcome.review
        assert review is not None
        assert outcome.absent_reason is None
        assert review.verdict is PlanReviewVerdict.CONCERNS
        assert review.reviewers[0].findings[0].detail == "item 3 is two items"

    async def test_every_reviewer_failing_on_its_provider_raises(self) -> None:
        """A plan nobody could review must not park as one nobody objected to.

        The whole panel failing is an outage, not a quiet panel, so plan
        preparation fails and the caller marks the plan FAILED with the reason.
        """
        provider = ScriptedProvider(error=RuntimeError("provider down"))
        panel = _panel(provider)
        reviewer = _agent("cto", role="CTO")

        with pytest.raises(PlanReviewUnavailableError, match="not reviewed at all"):
            await panel.review(
                task=_task(), plan=_plan(), agents=(reviewer,), owner=None
            )
