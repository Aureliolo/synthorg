"""Tests for ``build_signals_service`` and its optional collaborators."""

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.budget.cost_record import CostRecord
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.meta.signal_models import OrgBudgetSummary
from synthorg.meta.signals.budget import BudgetSignalAggregator
from synthorg.meta.signals.coordination import CoordinationSignalAggregator
from synthorg.meta.signals.errors import ErrorSignalAggregator
from synthorg.meta.signals.evolution import EvolutionSignalAggregator
from synthorg.meta.signals.factory import build_signals_service
from synthorg.meta.signals.performance import PerformanceSignalAggregator
from synthorg.meta.signals.service import SignalsService
from synthorg.meta.signals.snapshot import SnapshotBuilder
from synthorg.meta.signals.telemetry import TelemetrySignalAggregator
from tests._shared import mock_of

pytestmark = pytest.mark.unit


async def _empty_provider(since: datetime, until: datetime) -> tuple[CostRecord, ...]:
    del since, until
    return ()


def _tracker() -> PerformanceTracker:
    return cast(PerformanceTracker, mock_of[PerformanceTracker]())


def _approval_store() -> ApprovalStoreProtocol:
    return cast(ApprovalStoreProtocol, mock_of[ApprovalStoreProtocol]())


class TestBuildSignalsService:
    def test_returns_signals_service(self) -> None:
        service = build_signals_service(
            performance_tracker=_tracker(),
            agent_ids_provider=tuple,
            approval_store=_approval_store(),
        )
        assert isinstance(service, SignalsService)

    def test_every_domain_reports_available(self) -> None:
        """No domain degrades: each aggregates from an always-wired source."""
        service = build_signals_service(
            performance_tracker=_tracker(),
            agent_ids_provider=tuple,
            approval_store=_approval_store(),
        )
        assert all(service.domain_availability().values())


class TestUnwiredStoreDegradation:
    async def test_snapshot_builds_without_optional_stores(self) -> None:
        """An absent error / evolution / telemetry store yields empty summaries."""
        builder = SnapshotBuilder(
            performance=PerformanceSignalAggregator(
                tracker=_tracker(), agent_ids_provider=tuple
            ),
            budget=BudgetSignalAggregator(cost_record_provider=_empty_provider),
            coordination=CoordinationSignalAggregator(),
            errors=ErrorSignalAggregator(),
            evolution=EvolutionSignalAggregator(),
            telemetry=TelemetrySignalAggregator(),
        )
        until = datetime(2026, 6, 1, tzinfo=UTC)
        snapshot = await builder.build(since=until - timedelta(days=7), until=until)
        assert snapshot.budget == OrgBudgetSummary(
            total_spend=0.0,
            productive_ratio=0.0,
            coordination_ratio=0.0,
            system_ratio=0.0,
            forecast_confidence=0.0,
            orchestration_overhead=0.0,
        )
