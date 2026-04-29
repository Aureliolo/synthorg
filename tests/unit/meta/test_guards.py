"""Unit tests for meta-loop proposal guards."""

import pytest

from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.guards.approval_gate import ApprovalGateGuard
from synthorg.meta.guards.rate_limit import RateLimitGuard
from synthorg.meta.guards.rollback_plan import RollbackPlanGuard
from synthorg.meta.guards.scope_check import ScopeCheckGuard
from synthorg.meta.models import (
    ArchitectureChange,
    ConfigChange,
    GuardVerdict,
    ImprovementProposal,
    ProposalAltitude,
    ProposalRationale,
    RollbackOperation,
    RollbackPlan,
)

pytestmark = pytest.mark.unit


def _rationale() -> ProposalRationale:
    return ProposalRationale(
        signal_summary="test",
        pattern_detected="test",
        expected_impact="test",
        confidence_reasoning="test",
    )


def _rollback() -> RollbackPlan:
    return RollbackPlan(
        operations=(
            RollbackOperation(
                operation_type="revert",
                target="x",
                description="revert x",
            ),
        ),
        validation_check="check x",
    )


def _config_proposal(
    altitude: ProposalAltitude = ProposalAltitude.CONFIG_TUNING,
) -> ImprovementProposal:
    if altitude == ProposalAltitude.CONFIG_TUNING:
        return ImprovementProposal(
            altitude=altitude,
            title="test",
            description="test",
            rationale=_rationale(),
            config_changes=(
                ConfigChange(
                    path="a.b",
                    old_value=1,
                    new_value=2,
                    description="d",
                ),
            ),
            rollback_plan=_rollback(),
            confidence=0.8,
        )
    if altitude == ProposalAltitude.ARCHITECTURE:
        return ImprovementProposal(
            altitude=altitude,
            title="test",
            description="test",
            rationale=_rationale(),
            architecture_changes=(
                ArchitectureChange(
                    operation="create_role",
                    target_name="x",
                    description="d",
                ),
            ),
            rollback_plan=_rollback(),
            confidence=0.8,
        )
    if altitude == ProposalAltitude.CODE_MODIFICATION:
        from synthorg.meta.models import CodeChange, CodeOperation

        return ImprovementProposal(
            altitude=altitude,
            title="test",
            description="test",
            rationale=_rationale(),
            code_changes=(
                CodeChange(
                    file_path="src/x.py",
                    operation=CodeOperation.CREATE,
                    new_content="content",
                    description="d",
                    reasoning="r",
                ),
            ),
            rollback_plan=_rollback(),
            confidence=0.8,
        )
    # PROMPT_TUNING
    from synthorg.meta.models import PromptChange

    return ImprovementProposal(
        altitude=altitude,
        title="test",
        description="test",
        rationale=_rationale(),
        prompt_changes=(
            PromptChange(
                principle_text="test",
                target_scope="all",
                description="d",
            ),
        ),
        rollback_plan=_rollback(),
        confidence=0.8,
    )


# ── ScopeCheckGuard ───────────────────────────────────────────────


class TestScopeCheckGuard:
    """Scope check guard tests."""

    async def test_config_tuning_enabled_passes(self) -> None:
        cfg = SelfImprovementConfig(
            enabled=True,
            config_tuning_enabled=True,
        )
        guard = ScopeCheckGuard(config=cfg)
        result = await guard.evaluate(_config_proposal(ProposalAltitude.CONFIG_TUNING))
        assert result.verdict == GuardVerdict.PASSED

    async def test_architecture_disabled_rejects(self) -> None:
        cfg = SelfImprovementConfig(
            enabled=True,
            architecture_proposals_enabled=False,
        )
        guard = ScopeCheckGuard(config=cfg)
        result = await guard.evaluate(_config_proposal(ProposalAltitude.ARCHITECTURE))
        assert result.verdict == GuardVerdict.REJECTED
        assert result.reason is not None
        assert "architecture" in result.reason

    async def test_prompt_tuning_enabled_passes(self) -> None:
        cfg = SelfImprovementConfig(
            enabled=True,
            prompt_tuning_enabled=True,
        )
        guard = ScopeCheckGuard(config=cfg)
        result = await guard.evaluate(_config_proposal(ProposalAltitude.PROMPT_TUNING))
        assert result.verdict == GuardVerdict.PASSED

    async def test_code_modification_disabled_rejects(self) -> None:
        cfg = SelfImprovementConfig(
            enabled=True,
            code_modification_enabled=False,
        )
        guard = ScopeCheckGuard(config=cfg)
        result = await guard.evaluate(
            _config_proposal(ProposalAltitude.CODE_MODIFICATION),
        )
        assert result.verdict == GuardVerdict.REJECTED
        assert result.reason is not None
        assert "code_modification" in result.reason

    async def test_code_modification_enabled_passes(self) -> None:
        from synthorg.meta.config import CodeModificationConfig

        cfg = SelfImprovementConfig(
            enabled=True,
            code_modification_enabled=True,
            code_modification=CodeModificationConfig(
                github_token="test-token",
                github_repo="test/repo",
            ),
        )
        guard = ScopeCheckGuard(config=cfg)
        result = await guard.evaluate(
            _config_proposal(ProposalAltitude.CODE_MODIFICATION),
        )
        assert result.verdict == GuardVerdict.PASSED


# ── RollbackPlanGuard ─────────────────────────────────────────────


class TestRollbackPlanGuard:
    """Rollback plan guard tests."""

    async def test_valid_plan_passes(self) -> None:
        guard = RollbackPlanGuard()
        result = await guard.evaluate(_config_proposal())
        assert result.verdict == GuardVerdict.PASSED

    async def test_guard_name(self) -> None:
        guard = RollbackPlanGuard()
        assert guard.name == "rollback_plan"


# ── RateLimitGuard ─────────────────────────────────────────────────


class TestRateLimitGuard:
    """Rate limit guard tests."""

    async def test_under_limit_passes(self) -> None:
        guard = RateLimitGuard(max_proposals=3, window_hours=1)
        result = await guard.evaluate(_config_proposal())
        assert result.verdict == GuardVerdict.PASSED

    async def test_at_limit_rejects(self) -> None:
        guard = RateLimitGuard(max_proposals=2, window_hours=1)
        await guard.evaluate(_config_proposal())
        await guard.evaluate(_config_proposal())
        result = await guard.evaluate(_config_proposal())
        assert result.verdict == GuardVerdict.REJECTED
        assert result.reason is not None
        assert "Rate limit" in result.reason

    async def test_guard_name(self) -> None:
        guard = RateLimitGuard()
        assert guard.name == "rate_limit"


# ── ApprovalGateGuard ─────────────────────────────────────────────


class TestApprovalGateGuard:
    """Approval gate guard tests."""

    async def test_guard_name(self) -> None:
        guard = ApprovalGateGuard()
        assert guard.name == "approval_gate"

    async def test_persists_proposal_and_passes(self) -> None:
        """Guard persists the proposal and passes on first evaluation."""
        from unittest.mock import AsyncMock

        from synthorg.approval.protocol import ApprovalStoreProtocol

        store = AsyncMock(spec=ApprovalStoreProtocol)
        guard = ApprovalGateGuard(approval_store=store)
        proposal = _config_proposal()

        result = await guard.evaluate(proposal)
        assert result.verdict == GuardVerdict.PASSED
        store.add.assert_awaited_once()

        # Inspect the persisted ApprovalItem
        (item,) = store.add.await_args.args
        assert item.action_type == f"proposal:{proposal.altitude.value}"
        assert item.title == proposal.title
        assert item.metadata["proposal_id"] == str(proposal.id)
        assert item.metadata["altitude"] == proposal.altitude.value

    async def test_replay_treated_as_idempotent_pass(self) -> None:
        """ConflictError from replay is treated as idempotent PASSED."""
        from unittest.mock import AsyncMock

        from synthorg.approval.protocol import ApprovalStoreProtocol
        from synthorg.core.domain_errors import ConflictError

        store = AsyncMock(spec=ApprovalStoreProtocol)
        store.add.side_effect = ConflictError("already exists")
        guard = ApprovalGateGuard(approval_store=store)

        result = await guard.evaluate(_config_proposal())
        assert result.verdict == GuardVerdict.PASSED

    async def test_missing_store_rejects(self) -> None:
        """Without a configured store the guard fails closed."""
        guard = ApprovalGateGuard(approval_store=None)
        result = await guard.evaluate(_config_proposal())
        assert result.verdict == GuardVerdict.REJECTED

    async def test_store_failure_rejects(self) -> None:
        """Non-duplicate store write errors fail the guard closed."""
        from unittest.mock import AsyncMock

        from synthorg.approval.protocol import ApprovalStoreProtocol

        store = AsyncMock(spec=ApprovalStoreProtocol)
        store.add.side_effect = RuntimeError("boom")
        guard = ApprovalGateGuard(approval_store=store)

        result = await guard.evaluate(_config_proposal())
        assert result.verdict == GuardVerdict.REJECTED

    def test_invalid_expiry_days_raises(self) -> None:
        """Guard construction rejects non-positive expiry windows."""
        with pytest.raises(ValueError, match="expiry_days"):
            ApprovalGateGuard(expiry_days=0)
