"""Tests for the agent-session stakeholder plan-review panel."""

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.plan_enums import PlanReviewVerdict
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
from synthorg.engine.plan_review.session import AgentSessionPlanReviewPanel
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
        """The reviewer reasoned and never submitted: no verdict, and a reason.

        The plan still parks (a greenlight is never blocked on the panel), but
        the operator is told it carries zero quality signal rather than shown
        an empty review section that reads like approval.
        """
        provider = ScriptedProvider([make_text_response("still thinking")])
        panel = _panel(provider)
        reviewer = _agent("cto", role="CTO")

        outcome = await panel.review(
            task=_task(), plan=_plan(), agents=(reviewer,), owner=None
        )

        assert outcome.review is None
        assert outcome.absent_reason is not None
        assert "no reviewer submitted a verdict" in outcome.absent_reason

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
