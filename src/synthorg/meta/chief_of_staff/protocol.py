"""Protocols for Chief of Staff advanced capabilities.

Defines structural interfaces for proposal outcome storage,
confidence adjustment, org-level inflection consumption, and
alert emission. All protocols are runtime-checkable.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.meta.chief_of_staff.models import (
        Alert,
        OrgInflection,
        OutcomeStats,
        ProposalOutcome,
    )
    from synthorg.meta.models import ImprovementProposal, ProposalAltitude


# audit-keep 2026-05-10: MemoryOutcomeStore impl in chief_of_staff/outcome_store.py
# + factory wiring + 3 consumers (service/learning/chat) + 4 test refs.
@runtime_checkable
class OutcomeStore(Protocol):
    """Store and retrieve proposal decision outcomes.

    Implementations persist ``ProposalOutcome`` records and
    aggregate them into ``OutcomeStats`` for the confidence
    learning pipeline.
    """

    async def record_outcome(
        self,
        outcome: ProposalOutcome,
    ) -> NotBlankStr:
        """Record a proposal decision as episodic memory.

        Args:
            outcome: The proposal outcome to record.

        Returns:
            Memory ID assigned by the backend.
        """
        ...

    async def get_stats(
        self,
        rule_name: NotBlankStr,
        altitude: ProposalAltitude,
    ) -> OutcomeStats | None:
        """Get aggregated approval stats for a rule/altitude pair.

        Returns ``None`` if fewer than ``min_outcomes`` decisions
        have been recorded for this combination.

        Args:
            rule_name: Name of the triggering rule.
            altitude: Proposal altitude to filter by.

        Returns:
            Aggregated statistics or None if insufficient data.
        """
        ...

    async def recent_outcomes(
        self,
        *,
        rule_name: NotBlankStr | None = None,
        altitude: ProposalAltitude | None = None,
        limit: int = 10,
    ) -> tuple[ProposalOutcome, ...]:
        """Retrieve recent outcomes with optional filtering.

        Args:
            rule_name: Filter by rule name.
            altitude: Filter by proposal altitude.
            limit: Maximum entries to return.

        Returns:
            Recent outcomes ordered by decision time (newest first).
        """
        ...


# audit-keep 2026-05-10: default impl + factory + 3 consumers
# (service/learning/factory) + 2 test refs.
@runtime_checkable
class ConfidenceAdjuster(Protocol):
    """Adjust proposal confidence based on historical patterns.

    Implementations blend the proposal's base confidence with
    historical approval rates from the ``OutcomeStore``.
    """

    @property
    def name(self) -> NotBlankStr:
        """Strategy name (e.g., ``"ema"``, ``"bayesian"``)."""
        ...

    async def adjust(
        self,
        proposal: ImprovementProposal,
        store: OutcomeStore,
    ) -> ImprovementProposal:
        """Return proposal with adjusted confidence.

        Must return the proposal unchanged when:
        - ``proposal.source_rule`` is ``None``
        - No historical stats are available

        Otherwise returns ``proposal.model_copy(update=...)``
        with confidence clamped to ``[0.0, 1.0]``.

        Args:
            proposal: Proposal to adjust.
            store: Outcome store for historical stats.

        Returns:
            Proposal with (possibly) adjusted confidence.
        """
        ...


# audit-keep 2026-05-10: public extension surface for downstream
# observability hooks (same shape as NotificationSink, TelemetrySubscriber).
@runtime_checkable
class OrgInflectionSink(Protocol):
    """Consumer of org-level inflection events.

    Implementations receive inflection events from the
    ``OrgInflectionMonitor`` and take action (e.g., emit
    proactive alerts, trigger early cycles).
    """

    async def on_inflection(self, inflection: OrgInflection) -> None:
        """Receive an org-level inflection event.

        Args:
            inflection: The detected inflection.
        """
        ...


# audit-keep 2026-05-10: public extension surface for proactive-alert
# plumbing in chief_of_staff/alerts.py.
@runtime_checkable
class AlertSink(Protocol):
    """Consumer of proactive alerts.

    Implementations handle alert emission (logging,
    notifications, webhooks, etc.).
    """

    async def on_alert(self, alert: Alert) -> None:
        """Receive a proactive alert.

        Args:
            alert: The alert to handle.
        """
        ...
