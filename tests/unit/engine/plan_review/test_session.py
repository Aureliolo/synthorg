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
from synthorg.engine.plan_review.models import PlanReviewPanelConfig
from synthorg.engine.plan_review.session import AgentSessionPlanReviewPanel
from synthorg.hr.seniority import SeniorityLevel
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


def _agent(label: str, *, role: str, level: SeniorityLevel) -> AgentIdentity:
    return make_e2e_identity(label=label).model_copy(
        update={"role": role, "department": "Executive", "level": level}
    )


def _panel(provider: ScriptedProvider) -> AgentSessionPlanReviewPanel:
    return AgentSessionPlanReviewPanel(
        provider=provider,
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
        reviewer = _agent("cfo", role="CFO", level=SeniorityLevel.C_SUITE)

        review = await panel.review(
            task=_task(), plan=_plan(), agents=(reviewer,), owner=None
        )

        assert review is not None
        assert review.verdict is PlanReviewVerdict.CONCERNS
        assert len(review.reviewers) == 1
        assert review.reviewers[0].reviewer_role == "CFO"
        assert review.reviewers[0].findings[0].detail == "over budget"

    async def test_no_panel_when_only_owner_available(self) -> None:
        provider = ScriptedProvider([])
        panel = _panel(provider)
        owner = _agent("owner", role="CTO", level=SeniorityLevel.C_SUITE)

        review = await panel.review(
            task=_task(), plan=_plan(), agents=(owner,), owner=owner
        )

        assert review is None
        assert provider.call_count == 0

    async def test_no_verdict_submitted_yields_no_review(self) -> None:
        # The reviewer reasons but never submits; the panel returns None rather
        # than fabricating a verdict.
        provider = ScriptedProvider([make_text_response("still thinking")])
        panel = _panel(provider)
        reviewer = _agent("cto", role="CTO", level=SeniorityLevel.C_SUITE)

        review = await panel.review(
            task=_task(), plan=_plan(), agents=(reviewer,), owner=None
        )

        assert review is None

    async def test_reviewer_session_failure_degrades_without_raising(self) -> None:
        # A failing reviewer session must never abort the whole gated-plan flow:
        # the panel degrades to no review (a greenlight is never blocked on it).
        provider = ScriptedProvider(error=RuntimeError("provider down"))
        panel = _panel(provider)
        reviewer = _agent("cto", role="CTO", level=SeniorityLevel.C_SUITE)

        review = await panel.review(
            task=_task(), plan=_plan(), agents=(reviewer,), owner=None
        )

        assert review is None
