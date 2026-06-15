"""Tests for build_signals_service and the optional scaling domain."""

from unittest.mock import MagicMock

import pytest

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.scaling.service import ScalingService
from synthorg.meta.signal_models import OrgScalingSummary
from synthorg.meta.signals.factory import build_signals_service
from synthorg.meta.signals.scaling import ScalingSignalAggregator
from synthorg.meta.signals.service import SignalsService
from synthorg.meta.signals.snapshot import SnapshotBuilder
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _tracker() -> PerformanceTracker:
    return mock_of[PerformanceTracker]()


def _approval_store() -> ApprovalStoreProtocol:
    return mock_of[ApprovalStoreProtocol]()


def _scaling_service() -> ScalingService:
    return mock_of[ScalingService](
        get_recent_decisions=MagicMock(return_value=()),
        get_recent_actions=MagicMock(return_value=()),
    )


class TestBuildSignalsService:
    def test_returns_signals_service(self) -> None:
        service = build_signals_service(
            performance_tracker=_tracker(),
            agent_ids_provider=tuple,
            approval_store=_approval_store(),
        )
        assert isinstance(service, SignalsService)

    def test_scaling_present_when_service_given(self) -> None:
        service = build_signals_service(
            performance_tracker=_tracker(),
            agent_ids_provider=tuple,
            approval_store=_approval_store(),
            scaling_service=_scaling_service(),
        )
        assert isinstance(service._scaling, ScalingSignalAggregator)

    def test_scaling_none_without_service(self) -> None:
        service = build_signals_service(
            performance_tracker=_tracker(),
            agent_ids_provider=tuple,
            approval_store=_approval_store(),
        )
        assert service._scaling is None


class TestScalingDegradation:
    async def test_get_scaling_history_empty_without_service(self) -> None:
        service = build_signals_service(
            performance_tracker=_tracker(),
            agent_ids_provider=tuple,
            approval_store=_approval_store(),
        )
        from datetime import UTC, datetime, timedelta

        until = datetime(2026, 6, 1, tzinfo=UTC)
        result = await service.get_scaling_history(
            since=until - timedelta(days=7), until=until
        )
        assert result == OrgScalingSummary()

    async def test_snapshot_builder_tolerates_no_scaling(self) -> None:
        from datetime import UTC, datetime, timedelta

        from synthorg.meta.signals.budget import BudgetSignalAggregator
        from synthorg.meta.signals.coordination import CoordinationSignalAggregator
        from synthorg.meta.signals.errors import ErrorSignalAggregator
        from synthorg.meta.signals.evolution import EvolutionSignalAggregator
        from synthorg.meta.signals.performance import PerformanceSignalAggregator
        from synthorg.meta.signals.telemetry import TelemetrySignalAggregator

        builder = SnapshotBuilder(
            performance=PerformanceSignalAggregator(
                tracker=_tracker(), agent_ids_provider=tuple
            ),
            budget=BudgetSignalAggregator(cost_record_provider=tuple),
            coordination=CoordinationSignalAggregator(),
            scaling=None,
            errors=ErrorSignalAggregator(),
            evolution=EvolutionSignalAggregator(),
            telemetry=TelemetrySignalAggregator(),
        )
        until = datetime(2026, 6, 1, tzinfo=UTC)
        snapshot = await builder.build(since=until - timedelta(days=7), until=until)
        assert snapshot.scaling == OrgScalingSummary()
