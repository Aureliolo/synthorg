"""Unit tests for the Chief-of-Staff-backed work refinement router."""

import pytest

from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.pipeline.models import WorkItem, WorkSource
from synthorg.meta.chief_of_staff.models import (
    ProposedApprovalSummary,
    ProposeResult,
)
from synthorg.meta.chief_of_staff.refinement import (
    ChiefOfStaffRefinementRouter,
    _to_handoff,
)
from tests._shared.scripted_provider import ScriptedProvider, make_text_response
from tests.unit.meta.chief_of_staff.propose_fakes import build_proposer

pytestmark = pytest.mark.unit

_CLARIFY_JSON = (
    '{"needs_clarification": true, '
    '"clarifying_question": "What does done look like for this?", '
    '"proposals": []}'
)
_PROPOSE_JSON = (
    '{"needs_clarification": false, "clarifying_question": null, '
    '"proposals": [{"title": "Build the board renderer", '
    '"raw_intent": "Render the grid and falling pieces", '
    '"project": "games", "priority": "high", '
    '"task_type": "development", "estimated_complexity": "medium", '
    '"acceptance_criteria": ["pieces render", "grid is visible"]}]}'
)


def _work_item() -> WorkItem:
    return WorkItem(
        origin_adapter_id="objective-entry-adapter",
        source=WorkSource.OBJECTIVE,
        title="Build me a Tetris clone",
        raw_intent="A browser game with rotation and scoring.",
        project="games",
        requested_by="user-1",
    )


def _task() -> Task:
    return Task(
        title=NotBlankStr("Build me a Tetris clone"),
        description=NotBlankStr("A browser game with rotation and scoring."),
        type=TaskType.DEVELOPMENT,
        project=NotBlankStr("games"),
        created_by=NotBlankStr("user-1"),
    )


class TestToHandoff:
    def test_maps_clarification(self) -> None:
        result = ProposeResult(
            conversation_id=NotBlankStr("conv-1"),
            status="needs_clarification",
            clarifying_question=NotBlankStr("Which audience?"),
        )

        handoff = _to_handoff(result)

        assert handoff.conversation_id == "conv-1"
        assert handoff.needs_clarification is True
        assert handoff.detail == "Which audience?"

    def test_maps_proposed(self) -> None:
        result = ProposeResult(
            conversation_id=NotBlankStr("conv-2"),
            status="proposed",
            proposals=(
                ProposedApprovalSummary(
                    approval_id=NotBlankStr("appr-1"),
                    proposal_id=NotBlankStr("prop-1"),
                    title=NotBlankStr("Build the renderer"),
                    task_type=TaskType.DEVELOPMENT,
                    priority=Priority.HIGH,
                ),
            ),
        )

        handoff = _to_handoff(result)

        assert handoff.conversation_id == "conv-2"
        assert handoff.needs_clarification is False
        assert "1 proposal(s)" in handoff.detail


class TestRequestRefinement:
    async def test_opens_a_clarifying_conversation(self) -> None:
        provider = ScriptedProvider(responses=[make_text_response(_CLARIFY_JSON)])
        proposer, conv_repo, turn_repo, _, _ = build_proposer(provider=provider)
        router = ChiefOfStaffRefinementRouter(proposer=proposer)

        handoff = await router.request_refinement(
            work_item=_work_item(),
            task=_task(),
            reasons=("no acceptance criteria defined",),
        )

        assert handoff.needs_clarification is True
        assert handoff.detail == "What does done look like for this?"
        # A real conversation was opened from the objective content.
        conv = conv_repo.items[handoff.conversation_id]
        assert conv.created_by == "user-1"
        user_turns = [t.content for t in turn_repo.turns if t.role.value == "user"]
        assert any("Build me a Tetris clone" in c for c in user_turns)

    async def test_parks_proposals_for_approval(self) -> None:
        provider = ScriptedProvider(responses=[make_text_response(_PROPOSE_JSON)])
        proposer, _, _, proposal_repo, approvals = build_proposer(provider=provider)
        router = ChiefOfStaffRefinementRouter(proposer=proposer)

        handoff = await router.request_refinement(
            work_item=_work_item(),
            task=_task(),
            reasons=("no acceptance criteria defined",),
        )

        assert handoff.needs_clarification is False
        assert "1 proposal(s)" in handoff.detail
        # The proposal is parked behind the human approval queue.
        assert len(await proposal_repo.list_items()) == 1
        assert len(await approvals.list_items()) == 1
