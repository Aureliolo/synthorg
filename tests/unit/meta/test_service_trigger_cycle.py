"""Tests for SelfImprovementService.trigger_cycle()."""

from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.errors import SelfImprovementTriggerError
from synthorg.meta.models import (
    ImprovementCycleResult,
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
)
from synthorg.meta.service import SelfImprovementService
from synthorg.observability.events.meta import (
    META_CYCLE_TRIGGER_FAILED,
    META_CYCLE_TRIGGERED,
)


def _snapshot() -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=7.5,
            avg_success_rate=0.85,
            avg_collaboration_score=6.0,
            agent_count=10,
        ),
        budget=OrgBudgetSummary(
            total_spend=10.0,
            productive_ratio=0.6,
            coordination_ratio=0.3,
            system_ratio=0.1,
            days_until_exhausted=None,
            forecast_confidence=0.5,
            orchestration_overhead=0.5,
        ),
        coordination=OrgCoordinationSummary(),
        errors=OrgErrorSummary(),
        evolution=OrgEvolutionSummary(),
        telemetry=OrgTelemetrySummary(),
    )


async def _builder() -> OrgSignalSnapshot:
    return _snapshot()


def _service(
    *,
    snapshot_builder: object | None = _builder,
) -> SelfImprovementService:
    cfg = SelfImprovementConfig(
        enabled=False,  # bypass approval-store guard
    )
    return SelfImprovementService(
        config=cfg,
        approval_store=AsyncMock(spec=ApprovalStoreProtocol),
        snapshot_builder=snapshot_builder,  # type: ignore[arg-type]
    )


class TestTriggerCycle:
    @pytest.mark.unit
    async def test_returns_cycle_result(self) -> None:
        service = _service()
        result = await service.trigger_cycle()
        assert isinstance(result, ImprovementCycleResult)
        assert result.proposals_count == len(result.proposals)
        assert result.completed_at >= result.started_at

    @pytest.mark.unit
    async def test_emits_triggered_event(self) -> None:
        service = _service()
        with structlog.testing.capture_logs() as logs:
            await service.trigger_cycle()
        events = [e for e in logs if e.get("event") == META_CYCLE_TRIGGERED]
        assert events, "expected META_CYCLE_TRIGGERED log entry"
        assert "cycle_id" in events[0]
        assert "proposals_count" in events[0]

    @pytest.mark.unit
    async def test_no_builder_raises(self) -> None:
        service = _service(snapshot_builder=None)
        with pytest.raises(SelfImprovementTriggerError):
            await service.trigger_cycle()

    @pytest.mark.unit
    async def test_no_builder_logs_trigger_failed(self) -> None:
        # Direct callers of the facade (notably the
        # ``synthorg_meta_trigger_cycle`` MCP tool) need a service-level
        # failure record before the exception propagates so they are
        # not left dark in the audit trail.
        service = _service(snapshot_builder=None)
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(SelfImprovementTriggerError),
        ):
            await service.trigger_cycle()
        events = [e for e in logs if e.get("event") == META_CYCLE_TRIGGER_FAILED]
        assert events, "expected META_CYCLE_TRIGGER_FAILED log entry"
        assert events[0]["reason"] == "no_snapshot_builder"

    @pytest.mark.unit
    async def test_builder_failure_propagates_and_logs(self) -> None:
        async def _boom_builder() -> OrgSignalSnapshot:
            msg = "snapshot builder crashed"
            raise RuntimeError(msg)

        service = _service(snapshot_builder=_boom_builder)
        # Runtime trigger failures are normalised into
        # ``SelfImprovementTriggerError`` so MCP callers can map them
        # to the ``unavailable`` domain code (same path as the
        # missing-builder precondition).
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(SelfImprovementTriggerError) as exc_info,
        ):
            await service.trigger_cycle()
        events = [e for e in logs if e.get("event") == META_CYCLE_TRIGGER_FAILED]
        assert events, "expected META_CYCLE_TRIGGER_FAILED log entry"
        assert events[0]["reason"] == "snapshot_builder_failed"
        assert events[0]["error_type"] == "RuntimeError"
        assert isinstance(exc_info.value.__cause__, RuntimeError)
