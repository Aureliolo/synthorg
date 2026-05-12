"""Protocols for cross-deployment analytics.

Defines structural interfaces for emitting anonymized events,
collecting and querying them, and generating threshold
recommendations. All protocols are runtime-checkable.
"""

from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.meta.chief_of_staff.models import ProposalOutcome
    from synthorg.meta.models import ImprovementProposal, RolloutResult
    from synthorg.meta.telemetry.models import (
        AggregatedPattern,
        AnonymizedOutcomeEvent,
        ThresholdRecommendation,
    )


_DEFAULT_MIN_DEPLOYMENTS: Final[int] = 3


# Impl in telemetry/emitter.py + telemetry/factory.py.
@runtime_checkable
class AnalyticsEmitter(Protocol):
    """Emits anonymized outcome events to a collector.

    Implementations buffer events and flush them in batches
    to the configured collector endpoint.
    """

    async def emit_decision(
        self,
        outcome: ProposalOutcome,
        *,
        proposal: ImprovementProposal,
    ) -> None:
        """Anonymize and buffer a proposal decision event.

        Args:
            outcome: The proposal outcome to anonymize.
            proposal: The decided proposal (for altitude/rule context).
        """
        ...

    async def emit_rollout(
        self,
        result: RolloutResult,
        *,
        proposal: ImprovementProposal,
    ) -> None:
        """Anonymize and buffer a rollout result event.

        Args:
            result: The rollout result to anonymize.
            proposal: The rolled-out proposal (for altitude/rule context).
        """
        ...

    async def flush(self) -> None:
        """Flush all buffered events to the collector immediately."""
        ...

    async def aclose(self) -> None:
        """Flush remaining events and release resources."""
        ...


# Impl in telemetry/collector.py + telemetry/factory.py;
# API controller consumer.
@runtime_checkable
class AnalyticsCollector(Protocol):
    """Receives and stores anonymized events from deployments.

    Implementations provide event ingestion and cross-deployment
    pattern querying.
    """

    async def ingest(
        self,
        events: tuple[AnonymizedOutcomeEvent, ...],
    ) -> int:
        """Ingest a batch of anonymized events.

        Args:
            events: Anonymized events to store.

        Returns:
            Number of events successfully ingested.
        """
        ...

    async def query_patterns(
        self,
        *,
        min_deployments: int = _DEFAULT_MIN_DEPLOYMENTS,
    ) -> tuple[AggregatedPattern, ...]:
        """Query cross-deployment patterns.

        Args:
            min_deployments: Minimum unique deployments required
                for a pattern to be included.

        Returns:
            Aggregated patterns sorted by deployment count.
        """
        ...


# Impl in telemetry/recommender.py + factory wiring.
@runtime_checkable
class RecommendationProvider(Protocol):
    """Generates threshold recommendations from aggregated patterns.

    Implementations analyze cross-deployment patterns and suggest
    improved default thresholds for new deployments.
    """

    async def get_recommendations(
        self,
        *,
        collector: AnalyticsCollector,
    ) -> tuple[ThresholdRecommendation, ...]:
        """Generate threshold recommendations from collected data.

        Args:
            collector: Collector to query patterns from.

        Returns:
            Threshold recommendations sorted by confidence.
        """
        ...
