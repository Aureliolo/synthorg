"""Code-modification overflow handler for service-access capability gaps.

A sandbox script cannot reach the internal service layer, so a recurring
gap whose capability is in ``service_access_capabilities`` cannot be an
authored sandbox tool. This handler routes such gaps to the existing
self-improvement ``CODE_MODIFICATION`` altitude: it frames the gap as a
:class:`RuleMatch` and delegates to a :class:`CodeModificationStrategy`,
which produces a draft-PR code-change proposal (not a same-run tool).

The strategy needs an :class:`OrgSignalSnapshot` for prompt context. A
caller may inject a live ``snapshot_provider``; otherwise a neutral
baseline snapshot is used, since the actionable signal here is the gap
itself (carried in ``RuleMatch.signal_context``), not org-wide metrics.
"""

from typing import TYPE_CHECKING

from synthorg.meta.models import (
    ProposalAltitude,
    RuleMatch,
    RuleSeverity,
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
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from synthorg.meta.models import ImprovementProposal
    from synthorg.meta.protocol import ImprovementStrategy
    from synthorg.meta.toolsmith.models import CapabilityGap

logger = get_logger(__name__)


def build_baseline_snapshot() -> OrgSignalSnapshot:
    """Build a neutral, zeroed org signal snapshot.

    Used when no live snapshot provider is wired: the gap (carried in the
    rule's ``signal_context``) is the actionable signal for an authoring
    decision, so org-wide metrics can be neutral.

    Returns:
        ``OrgSignalSnapshot`` instance.
    """
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


class CodeModificationOverflowHandler:
    """Adapts a capability gap into a ``CODE_MODIFICATION`` proposal.

    Args:
        strategy: The self-improvement code-modification strategy to
            delegate to (satisfies :class:`ImprovementStrategy`).
        snapshot_provider: Optional async source of a live
            :class:`OrgSignalSnapshot`; defaults to a neutral baseline.
    """

    def __init__(
        self,
        strategy: ImprovementStrategy,
        *,
        snapshot_provider: Callable[[], Awaitable[OrgSignalSnapshot]] | None = None,
    ) -> None:
        self._strategy = strategy
        self._snapshot_provider = snapshot_provider

    async def handle(self, gap: CapabilityGap) -> tuple[ImprovementProposal, ...]:
        """Frame the gap as a rule and delegate to the code-mod strategy.

        Returns:
            Tuple of the declared element types.
        """
        snapshot = (
            await self._snapshot_provider()
            if self._snapshot_provider is not None
            else build_baseline_snapshot()
        )
        rule = RuleMatch(
            rule_name="capability_gap_service_access",
            severity=RuleSeverity.WARNING,
            description=(
                f"Recurring capability gap {gap.signature!r} needs "
                f"service-layer access; routing to code modification."
            ),
            signal_context={
                "capability": gap.signature,
                "occurrences": gap.occurrences,
            },
            suggested_altitudes=(ProposalAltitude.CODE_MODIFICATION,),
        )
        return await self._strategy.propose(snapshot=snapshot, triggered_rules=(rule,))


__all__ = ["CodeModificationOverflowHandler", "build_baseline_snapshot"]
