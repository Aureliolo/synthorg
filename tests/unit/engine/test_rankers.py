"""Unit tests for ``CandidateRanker`` implementations."""

import pytest

from synthorg.engine.assignment.models import (
    AgentWorkload,
    AssignmentCandidate,
    AssignmentRequest,
)
from synthorg.engine.assignment.ranker_protocol import (
    CandidateRanker,
    RankingResult,
)
from synthorg.engine.assignment.rankers import (
    AuctionBidRanker,
    CostDescendingRanker,
    ScoreDescendingRanker,
    WorkloadAscendingRanker,
)

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
    """Build a request whose pool is the named agents (no workload data)."""
    return AssignmentRequest(
        task=make_assignment_task(),
        available_agents=tuple(make_assignment_agent(n) for n in names),
    )


def _request_with_workloads(
    *,
    candidates: list[AssignmentCandidate],
    workloads: dict[str, int] | None = None,
    costs: dict[str, float] | None = None,
) -> AssignmentRequest:
    """Build a request whose pool matches ``candidates`` plus optional workloads."""
    workloads = workloads or {}
    costs = costs or {}
    workload_tuple: tuple[AgentWorkload, ...] = tuple(
        AgentWorkload(
            agent_id=str(c.agent_identity.id),
            active_task_count=workloads.get(c.agent_identity.name, 0),
            total_cost=costs.get(c.agent_identity.name, 0.0),
        )
        for c in candidates
        if c.agent_identity.name in workloads or c.agent_identity.name in costs
    )
    return AssignmentRequest(
        task=make_assignment_task(),
        available_agents=tuple(c.agent_identity for c in candidates),
        workloads=workload_tuple,
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
        assert result.selected.agent_identity.name == "solo"
        assert result.selected.score == c.score
        assert result.alternatives == ()
        assert "Best match" in result.reason

    def test_picks_first_candidate(self) -> None:
        top = _candidate("top", 0.9)
        mid = _candidate("mid", 0.6)
        low = _candidate("low", 0.3)
        request = _request_with_pool("top", "mid", "low")

        result = ScoreDescendingRanker().rank([top, mid, low], request)

        assert result.selected.agent_identity.name == "top"
        assert [c.agent_identity.name for c in result.alternatives] == ["mid", "low"]
        assert "top" in result.reason
        assert "0.90" in result.reason

    def test_alternatives_preserve_input_order(self) -> None:
        c1 = _candidate("a", 0.8)
        c2 = _candidate("b", 0.5)
        c3 = _candidate("c", 0.5)  # tie with c2
        c4 = _candidate("d", 0.4)
        request = _request_with_pool("a", "b", "c", "d")

        result = ScoreDescendingRanker().rank([c1, c2, c3, c4], request)

        assert [c.agent_identity.name for c in result.alternatives] == ["b", "c", "d"]

    def test_accepts_tuple_input(self) -> None:
        c1 = _candidate("a", 0.7)
        c2 = _candidate("b", 0.5)
        request = _request_with_pool("a", "b")
        result = ScoreDescendingRanker().rank((c1, c2), request)
        assert result.selected.agent_identity.name == "a"
        assert [c.agent_identity.name for c in result.alternatives] == ["b"]


class TestWorkloadAscendingRanker:
    """Direct unit tests for ``WorkloadAscendingRanker.rank()``."""

    def test_implements_protocol(self) -> None:
        assert isinstance(WorkloadAscendingRanker(), CandidateRanker)

    def test_name(self) -> None:
        assert WorkloadAscendingRanker().name == "workload_ascending"

    def test_lowest_workload_wins(self) -> None:
        busy = _candidate("busy", 0.9)
        idle = _candidate("idle", 0.7)
        request = _request_with_workloads(
            candidates=[busy, idle],
            workloads={"busy": 5, "idle": 0},
        )
        result = WorkloadAscendingRanker().rank([busy, idle], request)
        assert result.selected.agent_identity.name == "idle"
        assert "Least loaded" in result.reason
        # Workload-balanced reason should include the score of the SELECTED agent.
        assert "0.70" in result.reason

    def test_tie_broken_by_score_descending(self) -> None:
        a = _candidate("a", 0.6)
        b = _candidate("b", 0.9)
        request = _request_with_workloads(
            candidates=[a, b],
            workloads={"a": 2, "b": 2},
        )
        # Pre-sort by score descending (the contract).
        result = WorkloadAscendingRanker().rank([b, a], request)
        assert result.selected.agent_identity.name == "b"

    def test_alternatives_sorted_by_score_not_workload(self) -> None:
        # Quirk preserved from prior LoadBalancedAssignmentStrategy:
        # selection by workload, alternatives by score.
        a = _candidate("low-w-low-s", 0.3)  # workload 0, score 0.3
        b = _candidate("med-w-high-s", 0.9)  # workload 2, score 0.9
        c = _candidate("hi-w-mid-s", 0.6)  # workload 5, score 0.6
        request = _request_with_workloads(
            candidates=[a, b, c],
            workloads={"low-w-low-s": 0, "med-w-high-s": 2, "hi-w-mid-s": 5},
        )
        result = WorkloadAscendingRanker().rank([b, c, a], request)
        # Selection: lowest workload = "low-w-low-s" (workload 0).
        assert result.selected.agent_identity.name == "low-w-low-s"
        # Alternatives: by score desc, NOT by workload.
        # b has score 0.9, c has score 0.6.
        assert [c.agent_identity.name for c in result.alternatives] == [
            "med-w-high-s",
            "hi-w-mid-s",
        ]

    def test_no_workload_data_falls_back_to_score(self) -> None:
        a = _candidate("a", 0.9)
        b = _candidate("b", 0.5)
        request = AssignmentRequest(
            task=make_assignment_task(),
            available_agents=(a.agent_identity, b.agent_identity),
        )
        result = WorkloadAscendingRanker().rank([a, b], request)
        assert result.selected.agent_identity.name == "a"
        assert "insufficient workload data" in result.reason

    def test_partial_workload_data_falls_back(self) -> None:
        # Only one of two candidates has workload data; falls back.
        a = _candidate("a", 0.9)
        b = _candidate("b", 0.5)
        request = _request_with_workloads(
            candidates=[a, b],
            workloads={"a": 3},  # b is missing
        )
        result = WorkloadAscendingRanker().rank([a, b], request)
        assert result.selected.agent_identity.name == "a"
        assert "insufficient workload data" in result.reason


class TestCostDescendingRanker:
    """Direct unit tests for ``CostDescendingRanker.rank()``."""

    def test_implements_protocol(self) -> None:
        assert isinstance(CostDescendingRanker(), CandidateRanker)

    def test_name(self) -> None:
        assert CostDescendingRanker().name == "cost_descending"

    def test_cheapest_wins(self) -> None:
        expensive = _candidate("expensive", 0.9)
        cheap = _candidate("cheap", 0.7)
        request = _request_with_workloads(
            candidates=[expensive, cheap],
            costs={"expensive": 100.0, "cheap": 5.0},
        )
        result = CostDescendingRanker().rank([expensive, cheap], request)
        assert result.selected.agent_identity.name == "cheap"
        assert "Cheapest" in result.reason

    def test_tie_broken_by_score_descending(self) -> None:
        a = _candidate("a", 0.6)
        b = _candidate("b", 0.9)
        request = _request_with_workloads(
            candidates=[a, b],
            costs={"a": 5.0, "b": 5.0},
        )
        result = CostDescendingRanker().rank([b, a], request)
        assert result.selected.agent_identity.name == "b"

    def test_alternatives_sorted_by_score_not_cost(self) -> None:
        # Cross-ranker consistency: alternatives by score desc.
        a = _candidate("low-cost-low-score", 0.3)
        b = _candidate("med-cost-high-score", 0.9)
        c = _candidate("hi-cost-mid-score", 0.6)
        request = _request_with_workloads(
            candidates=[a, b, c],
            costs={
                "low-cost-low-score": 1.0,
                "med-cost-high-score": 50.0,
                "hi-cost-mid-score": 100.0,
            },
        )
        result = CostDescendingRanker().rank([b, c, a], request)
        # Selection: lowest cost = "low-cost-low-score".
        assert result.selected.agent_identity.name == "low-cost-low-score"
        # Alternatives by score desc, NOT by cost.
        assert [c.agent_identity.name for c in result.alternatives] == [
            "med-cost-high-score",
            "hi-cost-mid-score",
        ]

    def test_no_cost_data_falls_back_to_score(self) -> None:
        a = _candidate("a", 0.9)
        b = _candidate("b", 0.5)
        request = AssignmentRequest(
            task=make_assignment_task(),
            available_agents=(a.agent_identity, b.agent_identity),
        )
        result = CostDescendingRanker().rank([a, b], request)
        assert result.selected.agent_identity.name == "a"
        assert "insufficient cost data" in result.reason


class TestAuctionBidRanker:
    """Direct unit tests for ``AuctionBidRanker.rank()``."""

    def test_implements_protocol(self) -> None:
        assert isinstance(AuctionBidRanker(), CandidateRanker)

    def test_name(self) -> None:
        assert AuctionBidRanker().name == "auction_bid"

    def test_no_workload_data_bid_equals_score(self) -> None:
        # Empty workload map => availability factor = 1.0 => bid == score.
        a = _candidate("a", 0.9)
        b = _candidate("b", 0.5)
        request = AssignmentRequest(
            task=make_assignment_task(),
            available_agents=(a.agent_identity, b.agent_identity),
        )
        result = AuctionBidRanker().rank([a, b], request)
        assert result.selected.agent_identity.name == "a"
        assert "Auction winner" in result.reason

    def test_idle_agent_with_lower_score_can_win(self) -> None:
        # Bid formula: bid = score * 1/(1+active_tasks).
        # high_score=0.8, busy=10  -> bid = 0.8 * 1/11 = 0.0727
        # low_score=0.5, idle=0    -> bid = 0.5 * 1/1  = 0.5
        # Idle wins.
        busy = _candidate("busy", 0.8)
        idle = _candidate("idle", 0.5)
        request = _request_with_workloads(
            candidates=[busy, idle],
            workloads={"busy": 10, "idle": 0},
        )
        result = AuctionBidRanker().rank([busy, idle], request)
        assert result.selected.agent_identity.name == "idle"

    def test_tie_on_bid_broken_by_score(self) -> None:
        # Constructed equal bids; score must break the tie.
        # a: score=0.6, workload=0 -> bid = 0.6
        # b: score=0.6, workload=0 -> bid = 0.6
        a = _candidate("a", 0.6)
        b = _candidate("b", 0.6)
        request = _request_with_workloads(
            candidates=[a, b],
            workloads={"a": 0, "b": 0},
        )
        # Stable sort + reverse=True with equal bids + equal scores: first wins.
        result = AuctionBidRanker().rank([a, b], request)
        assert result.selected.agent_identity.name == "a"

    def test_alternatives_sorted_by_score_not_bid(self) -> None:
        a = _candidate("a", 0.3)
        b = _candidate("b", 0.9)
        c = _candidate("c", 0.6)
        request = _request_with_workloads(
            candidates=[a, b, c],
            workloads={"a": 0, "b": 5, "c": 1},
        )
        # Bids: a=0.3*1=0.3, b=0.9*1/6=0.15, c=0.6*1/2=0.3
        # Selection (highest bid, score tiebreak): a vs c both 0.3, score 0.6 > 0.3.
        result = AuctionBidRanker().rank([b, c, a], request)
        assert result.selected.agent_identity.name == "c"
        # Alternatives: by score desc.
        assert [c.agent_identity.name for c in result.alternatives] == ["b", "a"]

    def test_partial_workload_falls_back_to_score(self) -> None:
        # When workload data does not cover all candidates, availability = 1.0
        # for everyone (so bid == score), and a fallback warning is logged.
        a = _candidate("a", 0.9)
        b = _candidate("b", 0.5)
        request = _request_with_workloads(
            candidates=[a, b],
            workloads={"a": 0},
        )
        result = AuctionBidRanker().rank([a, b], request)
        assert result.selected.agent_identity.name == "a"
