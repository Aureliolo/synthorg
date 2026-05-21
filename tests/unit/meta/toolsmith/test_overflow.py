"""Unit tests for the code-modification overflow handler."""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    ImprovementProposal,
    ProposalAltitude,
    ProposalRationale,
    RollbackOperation,
    RollbackPlan,
    RuleMatch,
)
from synthorg.meta.signal_models import OrgSignalSnapshot
from synthorg.meta.toolsmith.models import CapabilityGap
from synthorg.meta.toolsmith.overflow import (
    CodeModificationOverflowHandler,
    build_baseline_snapshot,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)


def _gap() -> CapabilityGap:
    return CapabilityGap(
        signature=NotBlankStr("billing:reconcile"),
        occurrences=4,
        first_seen=_NOW,
        last_seen=_NOW,
    )


def _code_proposal() -> ImprovementProposal:
    from synthorg.meta.models import CodeChange, CodeOperation

    return ImprovementProposal(
        altitude=ProposalAltitude.CODE_MODIFICATION,
        title="Add billing reconcile",
        description="Adds a service-layer reconcile capability.",
        rationale=ProposalRationale(
            signal_summary="gap",
            pattern_detected="gap",
            expected_impact="impact",
            confidence_reasoning="reason",
        ),
        code_changes=(
            CodeChange(
                file_path="src/synthorg/meta/strategies/x.py",
                operation=CodeOperation.CREATE,
                new_content="x = 1\n",
                description="new",
                reasoning="why",
            ),
        ),
        rollback_plan=RollbackPlan(
            operations=(
                RollbackOperation(
                    operation_type="revert_branch",
                    target="meta/code-mod/abc",
                    description="revert",
                ),
            ),
            validation_check="branch deleted",
        ),
        confidence=0.5,
    )


class _FakeStrategy:
    """Records the propose() call and returns a canned proposal."""

    def __init__(self) -> None:
        self.calls: list[tuple[OrgSignalSnapshot, tuple[RuleMatch, ...]]] = []

    @property
    def altitude(self) -> ProposalAltitude:
        return ProposalAltitude.CODE_MODIFICATION

    async def propose(
        self,
        *,
        snapshot: OrgSignalSnapshot,
        triggered_rules: tuple[RuleMatch, ...],
    ) -> tuple[ImprovementProposal, ...]:
        self.calls.append((snapshot, triggered_rules))
        return (_code_proposal(),)


class TestBaselineSnapshot:
    def test_constructs(self) -> None:
        snap = build_baseline_snapshot()
        assert isinstance(snap, OrgSignalSnapshot)
        assert snap.performance.agent_count == 0
        assert snap.budget.total_spend == pytest.approx(0.0)


class TestCodeModificationOverflowHandler:
    async def test_delegates_to_strategy_with_gap_rule(self) -> None:
        strategy = _FakeStrategy()
        handler = CodeModificationOverflowHandler(strategy)  # type: ignore[arg-type]

        proposals = await handler.handle(_gap())

        assert len(proposals) == 1
        assert proposals[0].altitude is ProposalAltitude.CODE_MODIFICATION
        assert len(strategy.calls) == 1
        _snapshot, rules = strategy.calls[0]
        assert len(rules) == 1
        rule = rules[0]
        assert ProposalAltitude.CODE_MODIFICATION in rule.suggested_altitudes
        assert rule.signal_context["capability"] == "billing:reconcile"
        assert rule.signal_context["occurrences"] == 4

    async def test_uses_injected_snapshot_provider(self) -> None:
        strategy = _FakeStrategy()
        sentinel = build_baseline_snapshot()

        async def _provider() -> OrgSignalSnapshot:
            return sentinel

        handler = CodeModificationOverflowHandler(
            strategy,  # type: ignore[arg-type]
            snapshot_provider=_provider,
        )
        await handler.handle(_gap())
        used_snapshot, _rules = strategy.calls[0]
        assert used_snapshot is sentinel
