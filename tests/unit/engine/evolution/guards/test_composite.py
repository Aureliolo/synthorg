"""Tests for CompositeGuard."""

from unittest.mock import AsyncMock

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.evolution.guards.composite import CompositeGuard
from synthorg.engine.evolution.models import (
    AdaptationAxis,
    AdaptationDecision,
    AdaptationProposal,
    AdaptationSource,
)
from synthorg.engine.evolution.protocols import AdaptationGuard


@pytest.mark.unit
class TestCompositeGuard:
    """Tests for CompositeGuard."""

    @pytest.fixture
    def mock_guard_approve(self) -> AsyncMock:
        """Create a mock AdaptationGuard that always approves."""
        guard = AsyncMock(spec=AdaptationGuard)
        guard.name = "MockGuardApprove"

        async def evaluate(proposal: AdaptationProposal) -> AdaptationDecision:
            return AdaptationDecision(
                proposal_id=proposal.id,
                approved=True,
                guard_name="MockGuardApprove",
                reason="All clear",
            )

        guard.evaluate = evaluate
        return guard

    @pytest.fixture
    def mock_guard_reject(self) -> AsyncMock:
        """Create a mock AdaptationGuard that always rejects."""
        guard = AsyncMock(spec=AdaptationGuard)
        guard.name = "MockGuardReject"

        async def evaluate(proposal: AdaptationProposal) -> AdaptationDecision:
            return AdaptationDecision(
                proposal_id=proposal.id,
                approved=False,
                guard_name="MockGuardReject",
                reason="Rejected",
            )

        guard.evaluate = evaluate
        return guard

    @pytest.fixture
    def proposal(self) -> AdaptationProposal:
        """Create a sample adaptation proposal."""
        agent_id: NotBlankStr = "agent-001"
        return AdaptationProposal(
            agent_id=agent_id,
            axis=AdaptationAxis.IDENTITY,
            description="Test adaptation",
            changes={},
            confidence=0.9,
            source=AdaptationSource.SUCCESS,
        )

    @pytest.mark.asyncio
    async def test_name_property(self, mock_guard_approve: AsyncMock) -> None:
        """Test that the name property is non-blank."""
        composite = CompositeGuard(guards=(mock_guard_approve,))
        assert len(composite.name) > 0
        assert "CompositeGuard" in composite.name

    @pytest.mark.asyncio
    async def test_evaluate_all_approve(
        self, mock_guard_approve: AsyncMock, proposal: AdaptationProposal
    ) -> None:
        """Test that composite approves when all guards approve."""
        guard1 = AsyncMock(spec=AdaptationGuard)

        async def evaluate1(p: AdaptationProposal) -> AdaptationDecision:
            return AdaptationDecision(
                proposal_id=p.id,
                approved=True,
                guard_name="Guard1",
                reason="OK",
            )

        guard1.evaluate = evaluate1

        guard2 = AsyncMock(spec=AdaptationGuard)

        async def evaluate2(p: AdaptationProposal) -> AdaptationDecision:
            return AdaptationDecision(
                proposal_id=p.id,
                approved=True,
                guard_name="Guard2",
                reason="OK",
            )

        guard2.evaluate = evaluate2

        composite = CompositeGuard(guards=(guard1, guard2))
        decision = await composite.evaluate(proposal)

        assert decision.approved is True
        assert decision.proposal_id == proposal.id

    @pytest.mark.asyncio
    async def test_evaluate_one_rejects(
        self,
        mock_guard_approve: AsyncMock,
        mock_guard_reject: AsyncMock,
        proposal: AdaptationProposal,
    ) -> None:
        """Test that composite rejects if any guard rejects."""
        composite = CompositeGuard(
            guards=(
                mock_guard_approve,
                mock_guard_reject,
            ),
        )
        decision = await composite.evaluate(proposal)

        assert decision.approved is False
        assert decision.guard_name == "MockGuardReject"

    @pytest.mark.asyncio
    async def test_evaluate_short_circuit_on_reject(
        self, proposal: AdaptationProposal
    ) -> None:
        """Test that composite short-circuits on first rejection."""
        guard1 = AsyncMock(spec=AdaptationGuard)

        async def evaluate1(p: AdaptationProposal) -> AdaptationDecision:
            return AdaptationDecision(
                proposal_id=p.id,
                approved=True,
                guard_name="Guard1",
                reason="OK",
            )

        guard1.evaluate = evaluate1

        guard2 = AsyncMock(spec=AdaptationGuard)

        async def evaluate2(p: AdaptationProposal) -> AdaptationDecision:
            return AdaptationDecision(
                proposal_id=p.id,
                approved=False,
                guard_name="Guard2",
                reason="Rejected",
            )

        guard2.evaluate = evaluate2

        guard3 = AsyncMock(spec=AdaptationGuard)

        async def evaluate3(p: AdaptationProposal) -> None:
            msg = "Should not be called"
            raise AssertionError(msg)

        guard3.evaluate = evaluate3

        composite = CompositeGuard(guards=(guard1, guard2, guard3))
        decision = await composite.evaluate(proposal)

        assert decision.approved is False
        assert decision.guard_name == "Guard2"

    @pytest.mark.asyncio
    async def test_evaluate_single_guard(
        self, mock_guard_approve: AsyncMock, proposal: AdaptationProposal
    ) -> None:
        """Test composite with a single guard."""
        composite = CompositeGuard(guards=(mock_guard_approve,))
        decision = await composite.evaluate(proposal)

        assert decision.approved is True

    @pytest.mark.asyncio
    async def test_evaluate_many_guards(self, proposal: AdaptationProposal) -> None:
        """Test composite with many guards that all approve."""
        guards: list[AsyncMock] = []
        for i in range(5):
            guard = AsyncMock(spec=AdaptationGuard)

            async def evaluate(
                p: AdaptationProposal, guard_id: int = i
            ) -> AdaptationDecision:
                return AdaptationDecision(
                    proposal_id=p.id,
                    approved=True,
                    guard_name=f"Guard{guard_id}",
                    reason="OK",
                )

            guard.evaluate = evaluate
            guards.append(guard)

        composite = CompositeGuard(guards=tuple(guards))
        decision = await composite.evaluate(proposal)

        assert decision.approved is True

    @pytest.mark.asyncio
    async def test_evaluate_empty_guards(self, proposal: AdaptationProposal) -> None:
        """Test composite with no guards (should approve)."""
        composite = CompositeGuard(guards=())
        decision = await composite.evaluate(proposal)

        assert decision.approved is True

    @pytest.mark.asyncio
    async def test_evaluate_rejection_reason_preserved(
        self,
        mock_guard_approve: AsyncMock,
        proposal: AdaptationProposal,
    ) -> None:
        """Test that rejection reason is preserved."""
        guard_reject = AsyncMock(spec=AdaptationGuard)

        async def evaluate(p: AdaptationProposal) -> AdaptationDecision:
            return AdaptationDecision(
                proposal_id=p.id,
                approved=False,
                guard_name="RejectGuard",
                reason="Custom rejection reason",
            )

        guard_reject.evaluate = evaluate

        composite = CompositeGuard(
            guards=(
                mock_guard_approve,
                guard_reject,
            ),
        )
        decision = await composite.evaluate(proposal)

        assert decision.approved is False
        assert decision.reason == "Custom rejection reason"
