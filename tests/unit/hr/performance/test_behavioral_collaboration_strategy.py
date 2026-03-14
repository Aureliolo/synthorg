"""Tests for BehavioralTelemetryStrategy."""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.behavioral_collaboration_strategy import (
    BehavioralTelemetryStrategy,
)

from .conftest import make_collab_metric

NOW = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)


@pytest.mark.unit
class TestBehavioralTelemetryStrategy:
    """BehavioralTelemetryStrategy scoring logic."""

    def _make_strategy(self) -> BehavioralTelemetryStrategy:
        return BehavioralTelemetryStrategy()

    async def test_name(self) -> None:
        assert self._make_strategy().name == "behavioral_telemetry"

    async def test_all_components_present(self) -> None:
        """All components present -> weighted average score."""
        strategy = self._make_strategy()
        records = (
            make_collab_metric(
                recorded_at=NOW,
                delegation_success=True,
                delegation_response_seconds=0.0,
                conflict_constructiveness=1.0,
                meeting_contribution=1.0,
                loop_triggered=False,
                handoff_completeness=1.0,
            ),
        )

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            records=records,
        )

        # All components at maximum -> score should be 10.0
        assert result.score == 10.0
        assert result.strategy_name == "behavioral_telemetry"
        assert len(result.component_scores) == 6
        assert result.confidence > 0.0

    async def test_some_components_none(self) -> None:
        """Some components None -> weights redistributed."""
        strategy = self._make_strategy()
        records = (
            make_collab_metric(
                recorded_at=NOW,
                delegation_success=True,
                # delegation_response_seconds=None -> skipped
                conflict_constructiveness=1.0,
                # meeting_contribution=None -> skipped
                loop_triggered=False,
                handoff_completeness=1.0,
            ),
        )

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            records=records,
        )

        # Only 4 out of 6 components present
        # All present components are at max -> score should be 10.0
        assert result.score == 10.0
        # Only non-None components in breakdown
        component_names = {name for name, _ in result.component_scores}
        assert "delegation_response_latency" not in component_names
        assert "meeting_contribution" not in component_names

    async def test_all_components_none(self) -> None:
        """Only loop_prevention available -> max score 10.0 on single component."""
        strategy = self._make_strategy()
        # Record with no optional components set (only loop_triggered=False)
        # But loop_prevention always returns a value if there are records
        # delegation_success is None, delegation_response is None,
        # conflict is None, meeting is None, handoff is None.
        # loop_prevention will still have a value (10.0 since no loops).
        # So we need records where loop_prevention is the only computed value.
        records = (
            make_collab_metric(
                recorded_at=NOW,
                loop_triggered=False,
            ),
        )

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            records=records,
        )

        # loop_prevention is the only available component (10.0)
        # With weight redistribution to only loop_prevention: 10.0
        assert result.score == 10.0
        assert result.confidence == 0.1  # 1 record / 10.0

    async def test_empty_records_neutral(self) -> None:
        """Empty records -> neutral 5.0 score, confidence 0.0."""
        strategy = self._make_strategy()

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            records=(),
        )

        assert result.score == 5.0
        assert result.confidence == 0.0
        assert result.component_scores == ()

    async def test_custom_role_weights(self) -> None:
        """Custom role_weights override default weights."""
        strategy = self._make_strategy()
        records = (
            make_collab_metric(
                recorded_at=NOW,
                delegation_success=True,
                delegation_response_seconds=0.0,
                conflict_constructiveness=1.0,
                meeting_contribution=1.0,
                loop_triggered=False,
                handoff_completeness=1.0,
            ),
        )

        custom_weights = {
            "delegation_success": 0.50,
            "delegation_response_latency": 0.10,
            "conflict_constructiveness": 0.10,
            "meeting_contribution": 0.10,
            "loop_prevention": 0.10,
            "handoff_completeness": 0.10,
        }

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            records=records,
            role_weights=custom_weights,
        )

        # All at max -> still 10.0 regardless of weight distribution
        assert result.score == 10.0

    async def test_mixed_success_rate(self) -> None:
        """Mixed delegation success produces proportional score."""
        strategy = self._make_strategy()
        records = (
            make_collab_metric(
                recorded_at=NOW,
                delegation_success=True,
            ),
            make_collab_metric(
                recorded_at=NOW,
                delegation_success=False,
            ),
        )

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            records=records,
        )

        # delegation_success: 50% -> 5.0
        component_dict = dict(result.component_scores)
        assert component_dict["delegation_success"] == 5.0

    async def test_loop_triggered_reduces_score(self) -> None:
        """Loop triggers reduce loop_prevention score."""
        strategy = self._make_strategy()
        records = (
            make_collab_metric(recorded_at=NOW, loop_triggered=True),
            make_collab_metric(recorded_at=NOW, loop_triggered=False),
        )

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            records=records,
        )

        component_dict = dict(result.component_scores)
        # 1 loop out of 2 records -> 50% clean -> 5.0
        assert component_dict["loop_prevention"] == 5.0

    async def test_confidence_scales_with_record_count(self) -> None:
        """Confidence increases with more records (capped at 1.0)."""
        strategy = self._make_strategy()

        records_5 = tuple(
            make_collab_metric(
                recorded_at=NOW,
                delegation_success=True,
            )
            for _ in range(5)
        )
        records_10 = tuple(
            make_collab_metric(
                recorded_at=NOW,
                delegation_success=True,
            )
            for _ in range(10)
        )
        records_15 = tuple(
            make_collab_metric(
                recorded_at=NOW,
                delegation_success=True,
            )
            for _ in range(15)
        )

        r5 = await strategy.score(
            agent_id=NotBlankStr("a"),
            records=records_5,
        )
        r10 = await strategy.score(
            agent_id=NotBlankStr("a"),
            records=records_10,
        )
        r15 = await strategy.score(
            agent_id=NotBlankStr("a"),
            records=records_15,
        )

        assert r5.confidence == 0.5  # 5/10
        assert r10.confidence == 1.0  # 10/10
        assert r15.confidence == 1.0  # capped at 1.0

    async def test_response_latency_normalization(self) -> None:
        """High response time -> lower latency score."""
        strategy = self._make_strategy()
        records = (
            make_collab_metric(
                recorded_at=NOW,
                delegation_response_seconds=300.0,  # max
            ),
        )

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            records=records,
        )

        component_dict = dict(result.component_scores)
        # 300s is the max -> normalized to 0.0
        assert component_dict["delegation_response_latency"] == 0.0
