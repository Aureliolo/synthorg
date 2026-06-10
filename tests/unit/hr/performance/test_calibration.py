"""Tests for PerformanceTracker.get_collaboration_calibration().

Pins the curated ``CollaborationCalibration`` shape: stable strategy
metadata + window labels + active override + sample-size counters.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.collaboration_override_store import (
    CollaborationOverrideStore,
)
from synthorg.hr.performance.config import PerformanceConfig
from synthorg.hr.performance.models import (
    CollaborationCalibration,
    CollaborationMetricRecord,
    CollaborationOverride,
)
from synthorg.hr.performance.tracker import PerformanceTracker


def _make_metric(agent_id: str, recorded_at: datetime) -> CollaborationMetricRecord:
    return CollaborationMetricRecord(
        agent_id=NotBlankStr(agent_id),
        recorded_at=recorded_at,
        delegation_success=True,
        delegation_response_seconds=30.0,
        conflict_constructiveness=0.8,
        meeting_contribution=0.75,
        loop_triggered=False,
        handoff_completeness=0.9,
    )


class TestCalibrationFacade:
    """get_collaboration_calibration returns stable curated shape."""

    @pytest.mark.unit
    async def test_default_strategy_metadata(self) -> None:
        tracker = PerformanceTracker(config=PerformanceConfig())

        calibration = await tracker.get_collaboration_calibration(
            NotBlankStr("agent-1"),
        )
        assert isinstance(calibration, CollaborationCalibration)
        assert calibration.agent_id == "agent-1"
        assert calibration.strategy_name == "behavioral_telemetry"
        # Behavioral strategy publishes its default weights.
        weight_names = {name for name, _ in calibration.component_weights}
        assert "delegation_success" in weight_names
        assert "handoff_completeness" in weight_names
        # Window sizes match the tracker config.
        assert len(calibration.window_sizes) >= 1
        assert calibration.sample_size == 0
        assert calibration.last_calibrated_at is None
        assert calibration.active_override is None

    @pytest.mark.unit
    async def test_sample_size_reflects_recorded_metrics(self) -> None:
        tracker = PerformanceTracker(config=PerformanceConfig())
        agent_id = "agent-2"
        ts1 = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)
        ts2 = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
        await tracker.record_collaboration_event(_make_metric(agent_id, ts1))
        await tracker.record_collaboration_event(_make_metric(agent_id, ts2))

        calibration = await tracker.get_collaboration_calibration(
            NotBlankStr(agent_id),
        )
        assert calibration.sample_size == 2
        assert calibration.last_calibrated_at == ts2

    @pytest.mark.unit
    async def test_active_override_propagates(self) -> None:
        store = CollaborationOverrideStore()
        agent_id = "agent-3"
        override = CollaborationOverride(
            id=uuid4(),
            agent_id=NotBlankStr(agent_id),
            score=9.5,
            reason="manual calibration after offsite",
            applied_by=NotBlankStr("alice"),
            applied_at=datetime(2026, 4, 20, 12, 0, tzinfo=UTC),
        )
        store.set_override(override)
        tracker = PerformanceTracker(
            config=PerformanceConfig(),
            override_store=store,
        )

        calibration = await tracker.get_collaboration_calibration(
            NotBlankStr(agent_id),
        )
        assert calibration.active_override is not None
        assert calibration.active_override.score == 9.5
        assert calibration.active_override.applied_by == "alice"

    @pytest.mark.unit
    async def test_strategy_name_exposed_when_swapped(self) -> None:
        """Stable shape: strategy_name reflects the active strategy."""

        class _StubStrategy:
            @property
            def name(self) -> str:
                return "stub-strategy"

            async def score(
                self,
                *,
                agent_id: NotBlankStr,
                records: tuple[CollaborationMetricRecord, ...],
                role_weights: dict[str, float] | None = None,
            ) -> object:
                return None

        tracker = PerformanceTracker(
            collaboration_strategy=_StubStrategy(),  # type: ignore[arg-type]
            config=PerformanceConfig(),
        )
        calibration = await tracker.get_collaboration_calibration(
            NotBlankStr("agent-9"),
        )
        assert calibration.strategy_name == "stub-strategy"
        # No describe() on stub -> empty weights, but the envelope still
        # carries a stable shape (other consumers must not break).
        assert calibration.component_weights == ()
