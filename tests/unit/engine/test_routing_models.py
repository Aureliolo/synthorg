"""Tests for task routing domain models."""

import pytest

from ai_company.core.agent import AgentIdentity  # noqa: TC001
from ai_company.core.enums import CoordinationTopology
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

    @pytest.mark.unit
    def test_selected_in_alternatives_rejected(
        self, sample_agent_with_personality: AgentIdentity
    ) -> None:
        """Selected candidate duplicated in alternatives is rejected."""
        candidate = RoutingCandidate(
            agent_identity=sample_agent_with_personality,
            score=0.9,
            reason="Match",
        )
        alt = RoutingCandidate(
            agent_identity=sample_agent_with_personality,
            score=0.5,
            reason="Also match",
        )
        with pytest.raises(ValueError, match="also appears in alternatives"):
            RoutingDecision(
                subtask_id="sub-1",
                selected_candidate=candidate,
                alternatives=(alt,),
                topology=CoordinationTopology.CENTRALIZED,
            )


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

    @pytest.mark.unit
    def test_duplicate_decision_ids_rejected(
        self, sample_agent_with_personality: AgentIdentity
    ) -> None:
        """Duplicate subtask IDs within decisions are rejected."""
        candidate = RoutingCandidate(
            agent_identity=sample_agent_with_personality,
            score=0.5,
            reason="Match",
        )
        decision_a = RoutingDecision(
            subtask_id="sub-1",
            selected_candidate=candidate,
            topology=CoordinationTopology.SAS,
        )
        decision_b = RoutingDecision(
            subtask_id="sub-1",
            selected_candidate=candidate,
            topology=CoordinationTopology.SAS,
        )
        with pytest.raises(ValueError, match="Duplicate subtask IDs in decisions"):
            RoutingResult(
                parent_task_id="task-1",
                decisions=(decision_a, decision_b),
                unroutable=(),
            )

    @pytest.mark.unit
    def test_duplicate_unroutable_ids_rejected(self) -> None:
        """Duplicate subtask IDs within unroutable are rejected."""
        with pytest.raises(ValueError, match="Duplicate subtask IDs in unroutable"):
            RoutingResult(
                parent_task_id="task-1",
                decisions=(),
                unroutable=("sub-1", "sub-1"),
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
        assert config.parallel_artifact_threshold == 4

    @pytest.mark.unit
    def test_auto_topology_rejected(self) -> None:
        """AUTO topology in defaults causes infinite resolution."""
        with pytest.raises(ValueError, match="cannot be AUTO"):
            AutoTopologyConfig(
                sequential_override=CoordinationTopology.AUTO,
            )
        with pytest.raises(ValueError, match="cannot be AUTO"):
            AutoTopologyConfig(
                parallel_default=CoordinationTopology.AUTO,
            )
        with pytest.raises(ValueError, match="cannot be AUTO"):
            AutoTopologyConfig(
                mixed_default=CoordinationTopology.AUTO,
            )
