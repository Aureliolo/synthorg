"""Unit tests for ``CandidateRanker`` implementations."""

import pytest

from synthorg.engine.assignment.models import (
    AssignmentCandidate,
    AssignmentRequest,
)
from synthorg.engine.assignment.ranker_protocol import (
    CandidateRanker,
    RankingResult,
)
from synthorg.engine.assignment.rankers import ScoreDescendingRanker

from .conftest import make_assignment_agent, make_assignment_task

pytestmark = pytest.mark.unit


def _candidate(name: str, score: float) -> AssignmentCandidate:
    """Build an ``AssignmentCandidate`` with score and a default reason."""
    return AssignmentCandidate(
        agent_identity=make_assignment_agent(name),
        score=score,
        reason=f"score={score:.2f}",
    )


def _request_with_pool(*names: str) -> AssignmentRequest:
    """Build a request whose pool is the named agents."""
    return AssignmentRequest(
        task=make_assignment_task(),
        available_agents=tuple(make_assignment_agent(n) for n in names),
    )


class TestScoreDescendingRanker:
    """``ScoreDescendingRanker`` is the pass-through ranker."""

    def test_implements_protocol(self) -> None:
        assert isinstance(ScoreDescendingRanker(), CandidateRanker)

    def test_name(self) -> None:
        assert ScoreDescendingRanker().name == "score_descending"

    def test_single_candidate_no_alternatives(self) -> None:
        c = _candidate("solo", 0.7)
        request = _request_with_pool("solo")
        result = ScoreDescendingRanker().rank([c], request)
        assert isinstance(result, RankingResult)
        assert result.selected is c
        assert result.alternatives == ()
        assert "Best match" in result.reason

    def test_picks_first_candidate(self) -> None:
        # Input is already sorted descending by score (per the
        # contract of score_and_filter_candidates).
        top = _candidate("top", 0.9)
        mid = _candidate("mid", 0.6)
        low = _candidate("low", 0.3)
        request = _request_with_pool("top", "mid", "low")

        result = ScoreDescendingRanker().rank([top, mid, low], request)

        assert result.selected is top
        assert result.alternatives == (mid, low)
        assert "top" in result.reason
        assert "0.90" in result.reason

    def test_alternatives_preserve_input_order(self) -> None:
        # The contract says callers pass score-descending input;
        # ScoreDescendingRanker passes alternatives through as-is.
        c1 = _candidate("a", 0.8)
        c2 = _candidate("b", 0.5)
        c3 = _candidate("c", 0.5)  # tie with c2
        c4 = _candidate("d", 0.4)
        request = _request_with_pool("a", "b", "c", "d")

        result = ScoreDescendingRanker().rank([c1, c2, c3, c4], request)

        assert result.alternatives == (c2, c3, c4)

    def test_accepts_tuple_input(self) -> None:
        # The Protocol uses Sequence so tuples work alongside lists.
        c1 = _candidate("a", 0.7)
        c2 = _candidate("b", 0.5)
        request = _request_with_pool("a", "b")
        result = ScoreDescendingRanker().rank((c1, c2), request)
        assert result.selected is c1
        assert result.alternatives == (c2,)
