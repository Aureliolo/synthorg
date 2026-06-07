"""Property-based tests for trajectory scoring (Hypothesis)."""

from datetime import date
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
    SkillSet,
)
from synthorg.core.role import Authority
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.engine.trajectory.models import CandidateResult
from synthorg.engine.trajectory.scorer import TrajectoryScorer
from synthorg.execution.turn import TurnRecord
from synthorg.hr.seniority import SeniorityLevel
from synthorg.providers.enums import FinishReason


def _make_context() -> AgentContext:
    identity = AgentIdentity(
        id=uuid4(),
        name="test-agent",
        role="Developer",
        department="Engineering",
        level=SeniorityLevel.JUNIOR,
        personality=PersonalityConfig(),
        skills=SkillSet(),
        authority=Authority(),
        model=ModelConfig(
            provider="test-provider",
            model_id="test-model",
        ),
        hiring_date=date(2026, 1, 1),
    )
    return AgentContext.from_identity(
        identity=identity,
        max_turns=10,
    )


_CTX = _make_context()


def _candidate(
    index: int,
    trace_tokens: int,
    vc: float | None = None,
) -> CandidateResult:
    turns = (
        TurnRecord(
            turn_number=1,
            input_tokens=50,
            output_tokens=trace_tokens,
            cost=0.01,
            finish_reason=FinishReason.STOP,
        ),
    )
    return CandidateResult(
        candidate_index=index,
        execution_result=ExecutionResult(
            context=_CTX,
            termination_reason=TerminationReason.COMPLETED,
            turns=turns,
        ),
        verbalized_confidence=vc,
        trace_tokens=trace_tokens,
    )


@pytest.mark.unit
class TestTrajectoryScorerProperties:
    """Property-based tests for TrajectoryScorer."""

    @given(
        tokens=st.lists(
            st.integers(min_value=1, max_value=10000),
            min_size=2,
            max_size=5,
        ),
    )
    def test_selection_is_deterministic(
        self,
        tokens: list[int],
    ) -> None:
        """Same inputs always produce the same selection."""
        scorer = TrajectoryScorer()
        candidates = tuple(_candidate(i, t) for i, t in enumerate(tokens))
        best1 = scorer.select_best(candidates)
        best2 = scorer.select_best(candidates)
        assert best1.candidate_index == best2.candidate_index

    @given(
        tokens=st.lists(
            st.integers(min_value=1, max_value=10000),
            min_size=2,
            max_size=5,
        ),
    )
    def test_joint_score_ordering_consistent(
        self,
        tokens: list[int],
    ) -> None:
        """Joint scores use the normalized formula."""
        scorer = TrajectoryScorer()
        candidates = tuple(_candidate(i, t) for i, t in enumerate(tokens))
        scores = scorer.score_candidates(candidates)
        for score in scores:
            if score.len_score == 0.0:
                expected = score.vc_score
            else:
                expected = score.vc_score * abs(score.len_score) + score.len_score
            assert score.joint_score == pytest.approx(expected)

    @given(
        tokens=st.lists(
            st.integers(min_value=1, max_value=10000),
            min_size=1,
            max_size=5,
        ),
    )
    def test_len_score_always_non_positive(
        self,
        tokens: list[int],
    ) -> None:
        """Len score is always <= 0."""
        scorer = TrajectoryScorer()
        candidates = tuple(_candidate(i, t) for i, t in enumerate(tokens))
        scores = scorer.score_candidates(candidates)
        for score in scores:
            assert score.len_score <= 0.0
