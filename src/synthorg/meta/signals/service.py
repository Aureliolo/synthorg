"""SignalsService -- thin facade for MCP signal handlers.

Composes the 7 per-domain aggregators with :class:`SnapshotBuilder`
and the approval store to expose one callable surface:

* 7 ``get_*`` methods return per-domain :class:`OrgErrorSummary` /
  :class:`OrgBudgetSummary` / etc. for ``[since, until)`` windows.
* :meth:`get_org_snapshot` fans the same window out to all 7
  aggregators via :class:`SnapshotBuilder`.
* :meth:`list_proposals` queries :class:`ApprovalStore` for items
  flagged with ``action_type=_PROPOSAL_ACTION_TYPE``.
* :meth:`submit_proposal` wraps an :class:`ImprovementProposal` into
  an :class:`ApprovalItem` and persists it (destructive: audit-logged,
  returns the created item).

Design notes:
- Aggregators are passed in explicit form so the facade does not need
  to know about the three underlying stores (error taxonomy, evolution
  outcomes, telemetry counter); that wiring belongs in the AppState
  factory.
- Proposals share the generic :class:`ApprovalStore` surface; the
  ``action_type`` discriminator ("signals.proposal") marks items that
  originate from the improvement cycle.  Listing filters by this
  action type so existing approval-queue tools remain unaffected.
"""

from datetime import datetime
from uuid import uuid4

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.core.approval import ApprovalItem
from synthorg.core.types import NotBlankStr
from synthorg.meta._proposal_altitude_descriptor import (
    PROPOSAL_ALTITUDE_DESCRIPTORS,
)
from synthorg.meta.models import ImprovementProposal
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
from synthorg.meta.signals.budget import BudgetSignalAggregator
from synthorg.meta.signals.coordination import CoordinationSignalAggregator
from synthorg.meta.signals.errors import ErrorSignalAggregator
from synthorg.meta.signals.evolution import EvolutionSignalAggregator
from synthorg.meta.signals.performance import PerformanceSignalAggregator
from synthorg.meta.signals.scaling import ScalingSignalAggregator
from synthorg.meta.signals.snapshot import SnapshotBuilder
from synthorg.meta.signals.telemetry import TelemetrySignalAggregator
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_PROPOSAL_LISTED,
    META_PROPOSAL_SUBMITTED,
)

logger = get_logger(__name__)

_PROPOSAL_ACTION_TYPE = "signals.proposal"
"""Discriminator used on approval items that originate from the signals
facade.  Listing filters by this type so the generic approval queue
stays clean.
"""


class SignalsService:
    """Facade over the 7 aggregators + snapshot builder + proposal store.

    Args:
        performance: Per-domain aggregator.
        budget: Per-domain aggregator.
        coordination: Per-domain aggregator.
        scaling: Per-domain aggregator, or ``None`` when no scaling
            service is wired (the scaling domain then degrades to an
            empty summary rather than 503-ing the whole facade).
        errors: Per-domain aggregator.
        evolution: Per-domain aggregator.
        telemetry: Per-domain aggregator.
        snapshot_builder: Composite snapshot builder over all 7.
        approval_store: Shared approval store used for proposal
            submission and listing (filtered by action_type).
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        performance: PerformanceSignalAggregator,
        budget: BudgetSignalAggregator,
        coordination: CoordinationSignalAggregator,
        scaling: ScalingSignalAggregator | None,
        errors: ErrorSignalAggregator,
        evolution: EvolutionSignalAggregator,
        telemetry: TelemetrySignalAggregator,
        snapshot_builder: SnapshotBuilder,
        approval_store: ApprovalStoreProtocol,
    ) -> None:
        self._performance = performance
        self._budget = budget
        self._coordination = coordination
        self._scaling = scaling
        self._errors = errors
        self._evolution = evolution
        self._telemetry = telemetry
        self._snapshot_builder = snapshot_builder
        self._approval_store = approval_store

    @property
    def snapshot_builder(self) -> SnapshotBuilder:
        """The composite snapshot builder backing this facade.

        Exposed so a sibling consumer (the org-inflection monitor) shares
        the same aggregator wiring rather than rebuilding a parallel one.

        Returns:
            The shared snapshot builder.
        """
        return self._snapshot_builder

    # ── Snapshot + per-domain reads ──────────────────────────────────

    async def get_org_snapshot(
        self,
        *,
        since: datetime,
        until: datetime | None = None,
    ) -> OrgSignalSnapshot:
        """Build a composite snapshot across all 7 domains.

        Returns:
            ``OrgSignalSnapshot`` instance.
        """
        return await self._snapshot_builder.build(since=since, until=until)

    async def get_performance(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgPerformanceSummary:
        """Performance signal summary for the window.

        Returns:
            ``OrgPerformanceSummary`` instance.
        """
        return await self._performance.aggregate(since=since, until=until)

    async def get_budget(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgBudgetSummary:
        """Budget signal summary for the window.

        Returns:
            ``OrgBudgetSummary`` instance.
        """
        return await self._budget.aggregate(since=since, until=until)

    async def get_coordination(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgCoordinationSummary:
        """Coordination metrics summary for the window.

        Returns:
            ``OrgCoordinationSummary`` instance.
        """
        return await self._coordination.aggregate(since=since, until=until)

    async def get_scaling_history(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgScalingSummary:
        """Scaling signal summary for the window.

        Returns:
            ``OrgScalingSummary`` instance, or an empty summary when no
            scaling service is wired.
        """
        if self._scaling is None:
            return OrgScalingSummary()
        return await self._scaling.aggregate(since=since, until=until)

    async def get_error_patterns(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgErrorSummary:
        """Error taxonomy summary for the window.

        Returns:
            ``OrgErrorSummary`` instance.
        """
        return await self._errors.aggregate(since=since, until=until)

    async def get_evolution_outcomes(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgEvolutionSummary:
        """Evolution outcome summary for the window.

        Returns:
            ``OrgEvolutionSummary`` instance.
        """
        return await self._evolution.aggregate(since=since, until=until)

    async def get_telemetry(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgTelemetrySummary:
        """Telemetry event summary for the window.

        Returns:
            ``OrgTelemetrySummary`` instance.
        """
        return await self._telemetry.aggregate(since=since, until=until)

    # ── Proposal read ────────────────────────────────────────────────

    async def list_proposals(
        self,
        *,
        status: ApprovalStatus | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[tuple[ApprovalItem, ...], int]:
        """Return proposals (approval items) flagged as signals-originated.

        Args:
            status: Optional approval status filter.
            offset: Starting offset (inclusive) into the ordered set.
            limit: Maximum number of items to return; ``None`` returns
                every item from ``offset`` onwards.

        Returns:
            Tuple of ``(page, total)`` where ``page`` is the slice of
            items newest-first and ``total`` is the unfiltered count
            before slicing so callers never have to reconstruct it.

        Raises:
            ValueError: If ``offset`` is negative, or ``limit`` is
                provided and non-positive.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit is not None and limit < 1:
            msg = f"limit must be >= 1 when provided, got {limit}"
            raise ValueError(msg)
        items = await self._approval_store.list_items(
            status=status,
            action_type=NotBlankStr(_PROPOSAL_ACTION_TYPE),
        )
        ordered = tuple(sorted(items, key=lambda a: a.created_at, reverse=True))
        total = len(ordered)
        end = total if limit is None else offset + limit
        page = ordered[offset:end]
        logger.info(
            META_PROPOSAL_LISTED,
            count=len(page),
            total=total,
            status=status.value if status is not None else None,
        )
        return page, total

    # ── Proposal write (destructive) ─────────────────────────────────

    async def submit_proposal(
        self,
        *,
        proposal: ImprovementProposal,
        actor: AgentIdentity,
        reason: NotBlankStr,
    ) -> ApprovalItem:
        """Wrap a proposal into an approval item and persist it.

        The handler is responsible for enforcing the destructive-op
        guardrails (confirm + reason + actor); this method logs the
        submission for audit and returns the stored item.

        Args:
            proposal: Validated improvement proposal.
            actor: Identity of the submitting agent or operator.
            reason: Non-blank destructive-op reason already validated
                by the handler.

        Returns:
            The stored :class:`ApprovalItem`.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        actor_name = getattr(actor, "name", None)
        if not isinstance(actor_name, str) or not actor_name.strip():
            actor_id = getattr(actor, "id", None)
            actor_name = str(actor_id) if actor_id is not None else ""
        if not actor_name or not actor_name.strip():
            msg = "actor must carry a non-blank name or id"
            raise ValueError(msg)

        item = ApprovalItem(
            id=uuid4(),
            action_type=NotBlankStr(_PROPOSAL_ACTION_TYPE),
            title=proposal.title,
            description=proposal.description,
            requested_by=NotBlankStr(actor_name),
            risk_level=_risk_from_altitude(proposal),
            status=ApprovalStatus.PENDING,
            created_at=proposal.proposed_at,
            metadata={
                "proposal_id": str(proposal.id),
                "altitude": proposal.altitude.value,
                "source_rule": proposal.source_rule or "",
                "submission_reason": reason,
            },
        )
        await self._approval_store.add(item)
        logger.info(
            META_PROPOSAL_SUBMITTED,
            proposal_id=str(proposal.id),
            altitude=proposal.altitude.value,
            actor=actor_name,
        )
        return item


def _risk_from_altitude(proposal: ImprovementProposal) -> ApprovalRiskLevel:
    """Map proposal altitude to approval risk tier.

    Reads :data:`PROPOSAL_ALTITUDE_DESCRIPTORS`, which is exhaustive over
    :class:`ProposalAltitude` by an import-time completeness guard, so a
    new enum member added without a descriptor fails at import rather
    than silently routing to the wrong tier here.

    Returns:
        ``ApprovalRiskLevel`` instance.
    """
    return PROPOSAL_ALTITUDE_DESCRIPTORS[proposal.altitude].risk_level


__all__ = [
    "SignalsService",
]
