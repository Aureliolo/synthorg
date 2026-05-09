"""Integration tests for the self-improvement meta-loop cycle.

Tests the full pipeline: signals -> rules -> strategies ->
guards -> approval -> rollout -> regression detection.
"""

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.models import (
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
    ProposalAltitude,
    ProposalStatus,
    RolloutOutcome,
)
from synthorg.meta.service import SelfImprovementService
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.integration


def _snap(
    *,
    quality: float = 7.5,
    success: float = 0.85,
    days_left: int | None = None,
    coord_ratio: float = 0.3,
    error_findings: int = 0,
) -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=quality,
            avg_success_rate=success,
            avg_collaboration_score=6.0,
            agent_count=10,
        ),
        budget=OrgBudgetSummary(
            total_spend=150.0,
            productive_ratio=0.6,
            coordination_ratio=coord_ratio,
            system_ratio=0.1,
            days_until_exhausted=days_left,
            forecast_confidence=0.8,
            orchestration_overhead=0.5,
        ),
        coordination=OrgCoordinationSummary(),
        scaling=OrgScalingSummary(),
        errors=OrgErrorSummary(total_findings=error_findings),
        evolution=OrgEvolutionSummary(),
        telemetry=OrgTelemetrySummary(),
    )


class TestMetaCycleIntegration:
    """End-to-end cycle: signals -> rules -> proposals -> guards."""

    async def test_quality_decline_produces_pending_proposal(
        self,
    ) -> None:
        """Scenario: quality declining triggers config tuning proposal.

        Signal pattern -> rule fires -> strategy generates proposal
        -> guard chain passes -> proposal ready for approval.
        """
        svc = SelfImprovementService(
            config=SelfImprovementConfig(
                enabled=True,
                config_tuning_enabled=True,
            ),
            approval_store=ApprovalStore(),
        )
        proposals = await svc.run_cycle(_snap(quality=4.0))

        assert len(proposals) >= 1
        proposal = next(
            p
            for p in proposals
            if p.source_rule == "quality_declining"
            and p.altitude == ProposalAltitude.CONFIG_TUNING
        )
        assert proposal.status == ProposalStatus.PENDING
        assert proposal.rollback_plan.operations
        assert proposal.confidence > 0.0

    async def test_budget_overrun_produces_critical_proposal(
        self,
    ) -> None:
        """Scenario: budget exhaustion imminent triggers proposal."""
        svc = SelfImprovementService(
            config=SelfImprovementConfig(
                enabled=True,
                config_tuning_enabled=True,
            ),
            approval_store=ApprovalStore(),
        )
        proposals = await svc.run_cycle(_snap(days_left=7))

        sources = {p.source_rule for p in proposals}
        assert "budget_overrun" in sources

    async def test_proposal_rollout_succeeds(self) -> None:
        """Scenario: approved proposal -> rollout -> success."""

        async def snapshot_builder() -> OrgSignalSnapshot:
            return _snap(quality=7.5, success=0.85)

        svc = SelfImprovementService(
            config=SelfImprovementConfig(
                enabled=True,
                config_tuning_enabled=True,
            ),
            clock=FakeClock(),
            snapshot_builder=snapshot_builder,
            approval_store=ApprovalStore(),
        )
        proposals = await svc.run_cycle(_snap(quality=4.0))
        assert len(proposals) >= 1
        proposal = next(
            p
            for p in proposals
            if p.source_rule == "quality_declining"
            and p.altitude == ProposalAltitude.CONFIG_TUNING
        )
        # Simulate approval via model_copy because the ApprovalStore
        # approval flow is not yet integrated in the test harness.
        approved = proposal.model_copy(
            update={
                "status": ProposalStatus.APPROVED,
                "decided_at": proposal.proposed_at,
                "decided_by": "test-approver",
                "decision_reason": "Integration test approval",
            },
        )
        result = await svc.execute_rollout(approved)
        assert result.outcome == RolloutOutcome.SUCCESS

    async def test_disabled_altitude_blocks_proposals(self) -> None:
        """Scenario: architecture altitude disabled -> proposals rejected."""
        svc = SelfImprovementService(
            config=SelfImprovementConfig(
                enabled=True,
                config_tuning_enabled=True,
                architecture_proposals_enabled=False,
            ),
            approval_store=ApprovalStore(),
        )
        # Coordination cost ratio suggests both config and architecture.
        proposals = await svc.run_cycle(_snap(coord_ratio=0.5))
        assert proposals, "expected at least one proposal for coord_ratio=0.5"
        for p in proposals:
            assert p.altitude != ProposalAltitude.ARCHITECTURE

    async def test_healthy_org_no_proposals(self) -> None:
        """Scenario: all signals healthy -> no rules fire -> no proposals."""
        svc = SelfImprovementService(
            config=SelfImprovementConfig(
                enabled=True,
                config_tuning_enabled=True,
            ),
            approval_store=ApprovalStore(),
        )
        proposals = await svc.run_cycle(_snap())
        assert proposals == ()

    async def test_multi_altitude_cycle(self) -> None:
        """Scenario: quality decline with all altitudes enabled."""
        svc = SelfImprovementService(
            config=SelfImprovementConfig(
                enabled=True,
                config_tuning_enabled=True,
                architecture_proposals_enabled=True,
                prompt_tuning_enabled=True,
            ),
            approval_store=ApprovalStore(),
        )
        proposals = await svc.run_cycle(_snap(quality=4.0))
        altitudes = {p.altitude for p in proposals}
        assert ProposalAltitude.CONFIG_TUNING in altitudes
        assert ProposalAltitude.PROMPT_TUNING in altitudes
