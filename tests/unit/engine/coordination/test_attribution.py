"""Unit tests for coordination attribution models.

Tests AgentContribution, CoordinationResultWithAttribution, and
FailureAttribution validation.
"""

import pytest
from pydantic import ValidationError

from synthorg.core.task_enums import CoordinationTopology
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.attribution import (
    AgentContribution,
    CoordinationResultWithAttribution,
    FailureAttribution,
)
from synthorg.engine.coordination.models import (
    CoordinationPhaseResult,
    CoordinationResult,
)


def _make_coord_result(
    *,
    parent_task_id: str = "task-1",
    topology: CoordinationTopology = CoordinationTopology.SAS,
    success: bool = True,
) -> CoordinationResult:
    """Build a minimal CoordinationResult for testing."""
    return CoordinationResult(
        parent_task_id=NotBlankStr(parent_task_id),
        topology=topology,
        phases=(
            CoordinationPhaseResult(
                phase=NotBlankStr("dispatch"),
                success=success,
                duration_seconds=1.0,
                error=None if success else "phase error",
            ),
        ),
        total_duration_seconds=1.0,
        total_cost=0.5,
    )


class TestAgentContribution:
    """Tests for the AgentContribution model."""

    @pytest.mark.unit
    def test_successful_contribution(self) -> None:
        """Score 1.0 requires no failure attribution."""
        contrib = AgentContribution(
            agent_id=NotBlankStr("agent-1"),
            subtask_id=NotBlankStr("subtask-1"),
            contribution_score=1.0,
        )
        assert contrib.contribution_score == 1.0
        assert contrib.failure_attribution is None
        assert contrib.evidence is None

    @pytest.mark.unit
    def test_failed_contribution_direct(self) -> None:
        """Score < 1.0 requires failure attribution."""
        contrib = AgentContribution(
            agent_id=NotBlankStr("agent-1"),
            subtask_id=NotBlankStr("subtask-1"),
            contribution_score=0.0,
            failure_attribution="direct",
            evidence="Tool invocation failed: timeout",
        )
        assert contrib.contribution_score == 0.0
        assert contrib.failure_attribution == "direct"
        assert contrib.evidence is not None

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "attribution",
        [
            "direct",
            "upstream_contamination",
            "coordination_overhead",
            "quality_gate",
        ],
    )
    def test_all_failure_attribution_values(
        self,
        attribution: FailureAttribution,
    ) -> None:
        """All failure attribution literals are accepted."""
        contrib = AgentContribution(
            agent_id=NotBlankStr("agent-1"),
            subtask_id=NotBlankStr("subtask-1"),
            contribution_score=0.5,
            failure_attribution=attribution,
        )
        assert contrib.failure_attribution == attribution

    @pytest.mark.unit
    def test_score_below_one_requires_attribution(self) -> None:
        """Score < 1.0 without failure_attribution is rejected."""
        with pytest.raises(ValueError, match="failure_attribution"):
            AgentContribution(
                agent_id=NotBlankStr("agent-1"),
                subtask_id=NotBlankStr("subtask-1"),
                contribution_score=0.5,
            )

    @pytest.mark.unit
    def test_score_one_with_attribution_rejected(self) -> None:
        """Score == 1.0 with failure_attribution set is rejected."""
        with pytest.raises(ValueError, match="failure_attribution"):
            AgentContribution(
                agent_id=NotBlankStr("agent-1"),
                subtask_id=NotBlankStr("subtask-1"),
                contribution_score=1.0,
                failure_attribution="direct",
            )

    @pytest.mark.unit
    def test_score_bounds_low(self) -> None:
        """Score below 0.0 is rejected."""
        with pytest.raises(ValueError, match="greater than or equal to 0"):
            AgentContribution(
                agent_id=NotBlankStr("agent-1"),
                subtask_id=NotBlankStr("subtask-1"),
                contribution_score=-0.1,
                failure_attribution="direct",
            )

    @pytest.mark.unit
    def test_score_bounds_high(self) -> None:
        """Score above 1.0 is rejected."""
        with pytest.raises(ValueError, match="less than or equal to 1"):
            AgentContribution(
                agent_id=NotBlankStr("agent-1"),
                subtask_id=NotBlankStr("subtask-1"),
                contribution_score=1.1,
            )

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """AgentContribution is immutable."""
        contrib = AgentContribution(
            agent_id=NotBlankStr("agent-1"),
            subtask_id=NotBlankStr("subtask-1"),
            contribution_score=1.0,
        )
        with pytest.raises(ValidationError, match="frozen"):
            contrib.contribution_score = 0.5  # type: ignore[misc]

    @pytest.mark.unit
    def test_evidence_max_length(self) -> None:
        """Evidence exceeding 500 chars is rejected."""
        with pytest.raises(ValueError, match="at most 500"):
            AgentContribution(
                agent_id=NotBlankStr("agent-1"),
                subtask_id=NotBlankStr("subtask-1"),
                contribution_score=0.0,
                failure_attribution="direct",
                evidence="x" * 501,
            )

    @pytest.mark.unit
    def test_evidence_at_max_length(self) -> None:
        """Evidence at exactly 500 chars is accepted."""
        contrib = AgentContribution(
            agent_id=NotBlankStr("agent-1"),
            subtask_id=NotBlankStr("subtask-1"),
            contribution_score=0.0,
            failure_attribution="direct",
            evidence="x" * 500,
        )
        assert len(contrib.evidence) == 500  # type: ignore[arg-type]


class TestCoordinationResultWithAttribution:
    """Tests for the CoordinationResultWithAttribution wrapper."""

    @pytest.mark.unit
    def test_wraps_result(self) -> None:
        """Wrapper preserves the original CoordinationResult."""
        result = _make_coord_result()
        wrapper = CoordinationResultWithAttribution(
            result=result,
            agent_contributions=(),
        )
        assert wrapper.result is result

    @pytest.mark.unit
    def test_is_success_delegates(self) -> None:
        """is_success delegates to the wrapped result."""
        success_wrapper = CoordinationResultWithAttribution(
            result=_make_coord_result(success=True),
            agent_contributions=(),
        )
        assert success_wrapper.is_success is True

        failure_wrapper = CoordinationResultWithAttribution(
            result=_make_coord_result(success=False),
            agent_contributions=(),
        )
        assert failure_wrapper.is_success is False

    @pytest.mark.unit
    def test_avg_contribution_score_empty(self) -> None:
        """Empty contributions yield 0.0 average."""
        wrapper = CoordinationResultWithAttribution(
            result=_make_coord_result(),
            agent_contributions=(),
        )
        assert wrapper.avg_contribution_score == 0.0

    @pytest.mark.unit
    def test_avg_contribution_score_computed(self) -> None:
        """Average of contribution scores is computed correctly."""
        wrapper = CoordinationResultWithAttribution(
            result=_make_coord_result(),
            agent_contributions=(
                AgentContribution(
                    agent_id=NotBlankStr("a1"),
                    subtask_id=NotBlankStr("s1"),
                    contribution_score=1.0,
                ),
                AgentContribution(
                    agent_id=NotBlankStr("a2"),
                    subtask_id=NotBlankStr("s2"),
                    contribution_score=0.0,
                    failure_attribution="direct",
                ),
            ),
        )
        assert wrapper.avg_contribution_score == pytest.approx(0.5)

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """CoordinationResultWithAttribution is immutable."""
        wrapper = CoordinationResultWithAttribution(
            result=_make_coord_result(),
            agent_contributions=(),
        )
        with pytest.raises(ValidationError, match="frozen"):
            wrapper.agent_contributions = ()  # type: ignore[misc]
