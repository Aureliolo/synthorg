"""Tests for the SignalsService facade."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from typeguard import suppress_type_checks

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.meta.models import (
    ConfigChange,
    ImprovementProposal,
    ProposalAltitude,
    ProposalRationale,
    RollbackOperation,
    RollbackPlan,
)
from synthorg.meta.signal_models import (
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
)
from synthorg.meta.signals.service import SignalsService
from tests._shared import as_uuid
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


def _empty_snapshot() -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=0.0,
            avg_success_rate=0.0,
            avg_collaboration_score=0.0,
            agent_count=0,
        ),
        budget=OrgBudgetSummary(
            total_spend=0.0,
            productive_ratio=0.0,
            coordination_ratio=0.0,
            system_ratio=0.0,
            forecast_confidence=0.0,
            orchestration_overhead=0.0,
        ),
        coordination=OrgCoordinationSummary(),
        scaling=OrgScalingSummary(),
        errors=OrgErrorSummary(),
        evolution=OrgEvolutionSummary(),
        telemetry=OrgTelemetrySummary(),
    )


def _proposal() -> ImprovementProposal:
    return ImprovementProposal(
        altitude=ProposalAltitude.CONFIG_TUNING,
        title="test",
        description="description",
        rationale=ProposalRationale(
            signal_summary="noisy errors",
            pattern_detected="high error rate",
            expected_impact="fewer errors",
            confidence_reasoning="backed by samples",
        ),
        rollback_plan=RollbackPlan(
            operations=(
                RollbackOperation(
                    operation_type="revert_config",
                    target="a.b",
                    description="revert",
                ),
            ),
            validation_check="config reverted",
        ),
        confidence=0.8,
        config_changes=(
            ConfigChange(
                path="a.b",
                old_value=1,
                new_value=2,
                description="tune value",
            ),
        ),
    )


@pytest.fixture
def approval_store() -> AsyncMock:
    store = AsyncMock()
    store.list_items = AsyncMock(return_value=())
    store.add = AsyncMock(return_value=None)
    return store


@pytest.fixture
def snapshot_builder() -> AsyncMock:
    builder = AsyncMock()
    builder.build = AsyncMock(return_value=_empty_snapshot())
    return builder


def _aggregator_with(summary: object) -> AsyncMock:
    """Return an AsyncMock aggregator whose ``.aggregate()`` yields ``summary``."""
    agg = AsyncMock()
    agg.aggregate = AsyncMock(return_value=summary)
    return agg


@pytest.fixture
def service(approval_store: AsyncMock, snapshot_builder: AsyncMock) -> SignalsService:
    return SignalsService(
        performance=_aggregator_with(
            OrgPerformanceSummary(
                avg_quality_score=0.0,
                avg_success_rate=0.0,
                avg_collaboration_score=0.0,
                agent_count=0,
            ),
        ),
        budget=_aggregator_with(
            OrgBudgetSummary(
                total_spend=0.0,
                productive_ratio=0.0,
                coordination_ratio=0.0,
                system_ratio=0.0,
                forecast_confidence=0.0,
                orchestration_overhead=0.0,
            ),
        ),
        coordination=_aggregator_with(OrgCoordinationSummary()),
        scaling=_aggregator_with(OrgScalingSummary()),
        errors=_aggregator_with(OrgErrorSummary()),
        evolution=_aggregator_with(OrgEvolutionSummary()),
        telemetry=_aggregator_with(OrgTelemetrySummary()),
        snapshot_builder=snapshot_builder,
        approval_store=approval_store,
    )


class TestSignalsServiceSnapshot:
    async def test_delegates_to_builder(
        self,
        service: SignalsService,
        snapshot_builder: AsyncMock,
    ) -> None:
        now = datetime.now(UTC)
        since = now - timedelta(hours=1)
        snap = await service.get_org_snapshot(since=since, until=now)
        assert isinstance(snap, OrgSignalSnapshot)
        snapshot_builder.build.assert_awaited_once_with(since=since, until=now)


class TestSignalsServicePerDomain:
    @pytest.mark.parametrize(
        ("method", "expected_type"),
        [
            ("get_performance", OrgPerformanceSummary),
            ("get_budget", OrgBudgetSummary),
            ("get_coordination", OrgCoordinationSummary),
            ("get_scaling_history", OrgScalingSummary),
            ("get_error_patterns", OrgErrorSummary),
            ("get_evolution_outcomes", OrgEvolutionSummary),
            ("get_telemetry", OrgTelemetrySummary),
        ],
    )
    async def test_returns_expected_summary_type(
        self,
        service: SignalsService,
        method: str,
        expected_type: type,
    ) -> None:
        now = datetime.now(UTC)
        result = await getattr(service, method)(
            since=now - timedelta(hours=1),
            until=now,
        )
        assert isinstance(result, expected_type)


class TestSignalsServiceProposals:
    async def test_list_proposals_filters_by_action_type(
        self,
        service: SignalsService,
        approval_store: AsyncMock,
    ) -> None:
        await service.list_proposals()
        approval_store.list_items.assert_awaited_once()
        kwargs = approval_store.list_items.call_args.kwargs
        assert kwargs["action_type"] == "signals.proposal"

    async def test_list_proposals_orders_newest_first(
        self,
        service: SignalsService,
        approval_store: AsyncMock,
    ) -> None:
        older = _approval_item(
            created_at=datetime.now(UTC) - timedelta(hours=2),
        )
        newer = _approval_item(
            created_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        approval_store.list_items = AsyncMock(return_value=(older, newer))
        page, total = await service.list_proposals()
        assert [item.id for item in page] == [newer.id, older.id]
        assert total == 2

    async def test_submit_proposal_persists(
        self,
        service: SignalsService,
        approval_store: AsyncMock,
    ) -> None:
        proposal = _proposal()
        actor = make_test_actor(name="cos")
        item = await service.submit_proposal(
            proposal=proposal,
            actor=actor,
            reason="ship",
        )
        approval_store.add.assert_awaited_once()
        assert item.action_type == "signals.proposal"
        assert item.requested_by == "cos"
        assert item.metadata["proposal_id"] == str(proposal.id)

    async def test_submit_proposal_rejects_actor_without_identity(
        self,
        service: SignalsService,
    ) -> None:
        proposal = _proposal()

        class _AnonActor:
            id = None
            name = ""

        # ``_AnonActor`` deliberately violates the ``AgentIdentity``
        # annotation to exercise the service's runtime attribution
        # guard, so typeguard is suppressed around the awaited call.
        with (
            suppress_type_checks(),
            pytest.raises(ValueError, match="non-blank name or id"),
        ):
            await service.submit_proposal(
                proposal=proposal,
                actor=_AnonActor(),  # type: ignore[arg-type]
                reason="ship",
            )


def _approval_item(
    *,
    created_at: datetime,
    approval_id: str | None = None,
) -> ApprovalItem:
    return ApprovalItem(
        id=as_uuid(approval_id) if approval_id else uuid4(),
        action_type="signals.proposal",
        title="t",
        description="d",
        requested_by="r",
        risk_level=ApprovalRiskLevel.LOW,
        status=ApprovalStatus.PENDING,
        created_at=created_at,
    )
