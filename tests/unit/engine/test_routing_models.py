"""Tests for task routing domain models."""

from typing import TYPE_CHECKING

import pytest

from ai_company.core.enums import CoordinationTopology

if TYPE_CHECKING:
    from ai_company.core.agent import AgentIdentity
from ai_company.engine.routing.models import (
    AutoTopologyConfig,
    RoutingCandidate,
    RoutingDecision,
    RoutingResult,
)


class TestRoutingCandidate:
    """Tests for RoutingCandidate model."""

    @pytest.mark.unit
    def test_valid_candidate(
        self, sample_agent_with_personality: AgentIdentity
    ) -> None:
        """Valid candidate with score and reason."""
        candidate = RoutingCandidate(
            agent_identity=sample_agent_with_personality,
            score=0.8,
            matched_skills=("python",),
            reason="Good skill match",
        )
        assert candidate.score == 0.8
        assert candidate.matched_skills == ("python",)

    @pytest.mark.unit
    def test_score_bounds(self, sample_agent_with_personality: AgentIdentity) -> None:
        """Score must be between 0.0 and 1.0."""
        with pytest.raises(ValueError, match="less than or equal to 1"):
            RoutingCandidate(
                agent_identity=sample_agent_with_personality,
                score=1.5,
                reason="Invalid",
            )
        with pytest.raises(ValueError, match="greater than or equal to 0"):
            RoutingCandidate(
                agent_identity=sample_agent_with_personality,
                score=-0.1,
                reason="Invalid",
            )

    @pytest.mark.unit
    def test_frozen(self, sample_agent_with_personality: AgentIdentity) -> None:
        """RoutingCandidate is immutable."""
        candidate = RoutingCandidate(
            agent_identity=sample_agent_with_personality,
            score=0.5,
            reason="Test",
        )
        with pytest.raises(Exception, match="frozen"):
            candidate.score = 0.9  # type: ignore[misc]


class TestRoutingDecision:
    """Tests for RoutingDecision model."""

    @pytest.mark.unit
    def test_valid_decision(self, sample_agent_with_personality: AgentIdentity) -> None:
        """Valid routing decision with candidate and topology."""
        candidate = RoutingCandidate(
            agent_identity=sample_agent_with_personality,
            score=0.7,
            reason="Match",
        )
        decision = RoutingDecision(
            subtask_id="sub-1",
            selected_candidate=candidate,
            topology=CoordinationTopology.CENTRALIZED,
        )
        assert decision.subtask_id == "sub-1"
        assert decision.alternatives == ()


class TestRoutingResult:
    """Tests for RoutingResult model."""

    @pytest.mark.unit
    def test_valid_result(self) -> None:
        """Valid result with no overlap between decisions and unroutable."""
        result = RoutingResult(
            parent_task_id="task-1",
            decisions=(),
            unroutable=("sub-1",),
        )
        assert result.unroutable == ("sub-1",)

    @pytest.mark.unit
    def test_overlap_rejected(
        self, sample_agent_with_personality: AgentIdentity
    ) -> None:
        """Subtask in both decisions and unroutable is rejected."""
        candidate = RoutingCandidate(
            agent_identity=sample_agent_with_personality,
            score=0.5,
            reason="Match",
        )
        decision = RoutingDecision(
            subtask_id="sub-1",
            selected_candidate=candidate,
            topology=CoordinationTopology.SAS,
        )
        with pytest.raises(ValueError, match="both decisions and unroutable"):
            RoutingResult(
                parent_task_id="task-1",
                decisions=(decision,),
                unroutable=("sub-1",),
            )


class TestAutoTopologyConfig:
    """Tests for AutoTopologyConfig model."""

    @pytest.mark.unit
    def test_defaults(self) -> None:
        """Default topology config values."""
        config = AutoTopologyConfig()
        assert config.sequential_override == CoordinationTopology.SAS
        assert config.parallel_default == CoordinationTopology.CENTRALIZED
        assert config.mixed_default == CoordinationTopology.CONTEXT_DEPENDENT
        assert config.parallel_tool_threshold == 4
