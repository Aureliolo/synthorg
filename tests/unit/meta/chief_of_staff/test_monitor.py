# mypy: disable-error-code="explicit-any,unused-awaitable"
"""Unit tests for OrgInflectionMonitor."""

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from synthorg.meta.chief_of_staff.inflection import OrgInflectionDetector
from synthorg.meta.chief_of_staff.models import OrgInflection
from synthorg.meta.chief_of_staff.monitor import OrgInflectionMonitor
from synthorg.meta.models import (
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
    RuleSeverity,
)
from synthorg.meta.signals.snapshot import SnapshotBuilder
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)


def _snap(quality: float = 7.5) -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=quality,
            avg_success_rate=0.85,
            avg_collaboration_score=6.0,
            agent_count=10,
        ),
        budget=OrgBudgetSummary(
            total_spend=150.0,
            productive_ratio=0.6,
            coordination_ratio=0.3,
            system_ratio=0.1,
            forecast_confidence=0.8,
            orchestration_overhead=0.5,
        ),
        coordination=OrgCoordinationSummary(),
        scaling=OrgScalingSummary(),
        errors=OrgErrorSummary(),
        evolution=OrgEvolutionSummary(),
        telemetry=OrgTelemetrySummary(),
    )


class _CollectingSink:
    """Test sink that collects inflections."""

    def __init__(self) -> None:
        self.inflections: list[OrgInflection] = []

    async def on_inflection(self, inflection: OrgInflection) -> None:
        self.inflections.append(inflection)


class TestOrgInflectionMonitor:
    """OrgInflectionMonitor lifecycle tests."""

    async def test_start_idempotent(self) -> None:
        builder = cast(
            Any,
            mock_of[SnapshotBuilder](
                build=AsyncMock(return_value=_snap()),
            ),
        )
        monitor = OrgInflectionMonitor(
            detector=OrgInflectionDetector(),
            snapshot_builder=builder,
            sinks=(),
            check_interval_minutes=60,
        )
        await monitor.start()
        task1 = monitor._task
        await monitor.start()
        assert monitor._task is task1
        await monitor.stop()

    async def test_stop_cleans_up_task(self) -> None:
        builder = cast(
            Any,
            mock_of[SnapshotBuilder](
                build=AsyncMock(return_value=_snap()),
            ),
        )
        monitor = OrgInflectionMonitor(
            detector=OrgInflectionDetector(),
            snapshot_builder=builder,
            sinks=(),
            check_interval_minutes=60,
        )
        await monitor.start()
        assert monitor._task is not None
        await monitor.stop()
        assert monitor._task is None

    async def test_stop_without_start(self) -> None:
        monitor = OrgInflectionMonitor(
            detector=OrgInflectionDetector(),
            snapshot_builder=mock_of[SnapshotBuilder](),
            sinks=(),
        )
        await monitor.stop()

    async def test_first_tick_initializes_snapshot(self) -> None:
        builder = cast(
            Any,
            mock_of[SnapshotBuilder](
                build=AsyncMock(return_value=_snap()),
            ),
        )
        detector = cast(Any, mock_of[OrgInflectionDetector]())
        monitor = OrgInflectionMonitor(
            detector=detector,
            snapshot_builder=builder,
            sinks=(),
        )
        await monitor._tick()
        assert monitor._last_snapshot is not None
        detector.detect.assert_not_called()

    async def test_second_tick_calls_detector(self) -> None:
        snap1 = _snap(quality=7.5)
        snap2 = _snap(quality=7.5)
        builder = cast(
            Any,
            mock_of[SnapshotBuilder](
                build=AsyncMock(side_effect=[snap1, snap2]),
            ),
        )
        detector = cast(
            Any,
            mock_of[OrgInflectionDetector](
                detect=AsyncMock(return_value=()),
            ),
        )
        monitor = OrgInflectionMonitor(
            detector=detector,
            snapshot_builder=builder,
            sinks=(),
        )
        await monitor._tick()
        await monitor._tick()
        detector.detect.assert_called_once_with(
            previous=snap1,
            current=snap2,
        )

    async def test_inflection_emitted_to_sinks(self) -> None:
        snap1 = _snap(quality=7.5)
        snap2 = _snap(quality=4.0)
        builder = cast(
            Any,
            mock_of[SnapshotBuilder](
                build=AsyncMock(side_effect=[snap1, snap2]),
            ),
        )
        inflection = OrgInflection(
            severity=RuleSeverity.WARNING,
            affected_domains=("performance",),
            metric_name="quality_score",
            old_value=7.5,
            new_value=4.0,
            description="Quality dropped",
            detected_at=_NOW,
        )
        detector = cast(
            Any,
            mock_of[OrgInflectionDetector](
                detect=AsyncMock(return_value=(inflection,)),
            ),
        )
        sink = _CollectingSink()
        monitor = OrgInflectionMonitor(
            detector=detector,
            snapshot_builder=builder,
            sinks=(sink,),
        )
        await monitor._tick()
        await monitor._tick()
        assert len(sink.inflections) == 1
        assert sink.inflections[0].metric_name == "quality_score"

    async def test_sink_error_does_not_crash(self) -> None:
        snap1 = _snap()
        snap2 = _snap(quality=4.0)
        builder = cast(
            Any,
            mock_of[SnapshotBuilder](
                build=AsyncMock(side_effect=[snap1, snap2]),
            ),
        )
        inflection = OrgInflection(
            severity=RuleSeverity.WARNING,
            affected_domains=("performance",),
            metric_name="quality_score",
            old_value=7.5,
            new_value=4.0,
            description="Quality dropped",
            detected_at=_NOW,
        )
        detector = cast(
            Any,
            mock_of[OrgInflectionDetector](
                detect=AsyncMock(return_value=(inflection,)),
            ),
        )

        failing_sink = AsyncMock()
        failing_sink.on_inflection.side_effect = ValueError("sink error")
        good_sink = _CollectingSink()

        monitor = OrgInflectionMonitor(
            detector=detector,
            snapshot_builder=builder,
            sinks=(failing_sink, good_sink),
        )
        await monitor._tick()
        await monitor._tick()
        assert len(good_sink.inflections) == 1
