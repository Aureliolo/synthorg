"""Unit tests for project-owner selection."""

from typing import cast

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.engine.pipeline._owner_selection import (
    OwnerSelectionMethod,
    select_project_owner,
)
from synthorg.engine.routing.models import RoutingCandidate
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.hr.seniority import SeniorityLevel
from tests._shared import as_uuid, mock_of
from tests._shared.scripted_provider import make_e2e_identity

pytestmark = pytest.mark.unit

_MIN_SCORE = 0.1


def _task() -> Task:
    return Task(
        id=as_uuid("obj-1"),
        title="Build a beachhead",
        description="Deliver the first end-to-end slice.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project="beachhead",
        created_by="ceo",
    )


def _scorer(*, candidate: RoutingCandidate) -> AgentTaskScorer:
    scorer = mock_of[AgentTaskScorer]()
    scorer.min_score = _MIN_SCORE
    scorer.score.return_value = candidate
    return cast("AgentTaskScorer", scorer)


class TestSelectProjectOwner:
    def test_empty_roster_yields_none(self) -> None:
        scorer = _scorer(
            candidate=RoutingCandidate(
                agent_identity=make_e2e_identity(), score=0.9, reason="x"
            )
        )
        assert select_project_owner(_task(), (), scorer=scorer) is None

    def test_scored_owner_wins_when_above_threshold(self) -> None:
        agent = make_e2e_identity(label="picked")
        scorer = _scorer(
            candidate=RoutingCandidate(
                agent_identity=agent, score=0.9, reason="strong match"
            )
        )
        owner = select_project_owner(_task(), (agent,), scorer=scorer)
        assert owner is not None
        assert owner.id == agent.id

    def test_falls_back_to_most_senior_below_threshold(self) -> None:
        mid = make_e2e_identity(label="mid")  # SeniorityLevel.MID
        senior = make_e2e_identity(label="senior").model_copy(
            update={"level": SeniorityLevel.SENIOR}
        )
        # Every candidate scores below the threshold, so selection falls back
        # to seniority and the senior agent is staffed.
        scorer = _scorer(
            candidate=RoutingCandidate(agent_identity=mid, score=0.0, reason="weak")
        )
        owner = select_project_owner(_task(), (mid, senior), scorer=scorer)
        assert owner is not None
        assert owner.id == senior.id

    def test_selection_method_enum_values(self) -> None:
        assert OwnerSelectionMethod.SCORED.value == "scored"
        assert OwnerSelectionMethod.SENIORITY_FALLBACK.value == "seniority_fallback"

    def test_scored_tie_breaks_deterministically_on_id(self) -> None:
        # Two agents with identical scores must resolve to a stable pick (the
        # higher lexicographic id), so owner selection is reproducible.
        a = make_e2e_identity(label="aaa")
        b = make_e2e_identity(label="zzz")

        def _score(agent: AgentIdentity, _subtask: object) -> RoutingCandidate:
            return RoutingCandidate(agent_identity=agent, score=0.5, reason="tie")

        scorer = mock_of[AgentTaskScorer]()
        scorer.min_score = _MIN_SCORE
        scorer.score.side_effect = _score
        expected = max((a, b), key=lambda ident: str(ident.id))
        owner = select_project_owner(_task(), (a, b), scorer=scorer)
        assert owner is not None
        assert owner.id == expected.id
