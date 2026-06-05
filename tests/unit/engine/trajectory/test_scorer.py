"""Tests for the trajectory scorer."""

import math

import pytest

from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.engine.trajectory.models import CandidateResult
from synthorg.engine.trajectory.scorer import TrajectoryScorer
from synthorg.execution.turn import TurnRecord
from synthorg.providers.enums import FinishReason


def _candidate(
    index: int,
    context: AgentContext,
    *,
    vc: float | None = None,
    trace_tokens: int = 100,
    fingerprints: tuple[str, ...] = (),
) -> CandidateResult:
    """Helper to build a CandidateResult."""
    turns = (
        TurnRecord(
            turn_number=1,
            input_tokens=50,
            output_tokens=trace_tokens,
            cost=0.01,
            tool_call_fingerprints=fingerprints,
            finish_reason=FinishReason.STOP,
        ),
    )
    return CandidateResult(
        candidate_index=index,
        execution_result=ExecutionResult(
            context=context,
            termination_reason=TerminationReason.COMPLETED,
            turns=turns,
        ),
        verbalized_confidence=vc,
        trace_tokens=trace_tokens,
    )


@pytest.mark.unit
class TestTrajectoryScorer:
    """TrajectoryScorer scoring and selection tests."""

    @pytest.fixture
    def scorer(self) -> TrajectoryScorer:
        return TrajectoryScorer()

    def test_single_candidate_selected(
        self,
        scorer: TrajectoryScorer,
        minimal_context: AgentContext,
    ) -> None:
        candidates = (_candidate(0, minimal_context),)
        best = scorer.select_best(candidates)
        assert best.candidate_index == 0

    def test_shorter_trace_preferred_without_vc(
        self,
        scorer: TrajectoryScorer,
        minimal_context: AgentContext,
    ) -> None:
        candidates = (
            _candidate(0, minimal_context, trace_tokens=500),
            _candidate(1, minimal_context, trace_tokens=100),
        )
        best = scorer.select_best(candidates)
        assert best.candidate_index == 1  # Shorter trace wins.

    def test_higher_vc_preferred(
        self,
        scorer: TrajectoryScorer,
        minimal_context: AgentContext,
    ) -> None:
        candidates = (
            _candidate(0, minimal_context, vc=90.0, trace_tokens=100),
            _candidate(1, minimal_context, vc=50.0, trace_tokens=100),
        )
        best = scorer.select_best(candidates)
        assert best.candidate_index == 0  # Higher VC wins.

    def test_vc_none_graceful_degradation(
        self,
        scorer: TrajectoryScorer,
        minimal_context: AgentContext,
    ) -> None:
        """When VC is None, scorer uses Len-only."""
        candidates = (
            _candidate(0, minimal_context, trace_tokens=200),
            _candidate(1, minimal_context, trace_tokens=100),
        )
        scores = scorer.score_candidates(candidates)
        for s in scores:
            assert s.vc_score == 0.0  # No VC -> 0.0

    def test_vc_score_log_space(
        self,
        scorer: TrajectoryScorer,
        minimal_context: AgentContext,
    ) -> None:
        candidates = (_candidate(0, minimal_context, vc=80.0, trace_tokens=100),)
        scores = scorer.score_candidates(candidates)
        expected_vc = math.log(80.0 / 100.0)
        assert scores[0].vc_score == pytest.approx(expected_vc)

    def test_zero_vc_floor(
        self,
        scorer: TrajectoryScorer,
        minimal_context: AgentContext,
    ) -> None:
        candidates = (_candidate(0, minimal_context, vc=0.0, trace_tokens=100),)
        scores = scorer.score_candidates(candidates)
        assert scores[0].vc_score == -100.0

    def test_consistency_filter_majority_vote(
        self,
        scorer: TrajectoryScorer,
        minimal_context: AgentContext,
    ) -> None:
        candidates = (
            _candidate(
                0,
                minimal_context,
                fingerprints=("read:abc",),
                trace_tokens=100,
            ),
            _candidate(
                1,
                minimal_context,
                fingerprints=("read:abc",),
                trace_tokens=100,
            ),
            _candidate(
                2,
                minimal_context,
                fingerprints=("write:xyz",),
                trace_tokens=100,
            ),
        )
        scores = scorer.score_candidates(candidates)
        assert scores[0].consistent is True
        assert scores[1].consistent is True
        assert scores[2].consistent is False

    def test_consistent_preferred_over_inconsistent(
        self,
        scorer: TrajectoryScorer,
        minimal_context: AgentContext,
    ) -> None:
        """Consistent candidate wins even with worse Len score."""
        candidates = (
            _candidate(
                0,
                minimal_context,
                fingerprints=("read:abc",),
                trace_tokens=500,
            ),
            _candidate(
                1,
                minimal_context,
                fingerprints=("read:abc",),
                trace_tokens=400,
            ),
            _candidate(
                2,
                minimal_context,
                fingerprints=("write:xyz",),
                trace_tokens=50,
            ),
        )
        best = scorer.select_best(candidates)
        # Candidate 2 has best Len but is inconsistent.
        assert best.candidate_index == 1

    def test_no_majority_all_consistent(
        self,
        scorer: TrajectoryScorer,
        minimal_context: AgentContext,
    ) -> None:
        """No strict majority -- all candidates marked consistent."""
        candidates = (
            _candidate(
                0,
                minimal_context,
                fingerprints=("a:1",),
                trace_tokens=200,
            ),
            _candidate(
                1,
                minimal_context,
                fingerprints=("b:2",),
                trace_tokens=100,
            ),
        )
        scores = scorer.score_candidates(candidates)
        # No majority (1 vs 1) -- all marked consistent.
        assert scores[0].consistent is True
        assert scores[1].consistent is True
        best = scorer.select_best(candidates)
        # Shorter trace wins since both are consistent.
        assert best.candidate_index == 1

    def test_empty_candidates_raises(
        self,
        scorer: TrajectoryScorer,
    ) -> None:
        with pytest.raises(ValueError, match="empty"):
            scorer.score_candidates(())

    def test_empty_select_raises(
        self,
        scorer: TrajectoryScorer,
    ) -> None:
        with pytest.raises(ValueError, match="empty"):
            scorer.select_best(())
