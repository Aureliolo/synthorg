"""Drift detection strategy protocol."""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.ontology.models import DriftReport


# LayeredDriftDetector impl in drift/layered.py; drift service consumer.
@runtime_checkable
class DriftDetectionStrategy(Protocol):
    """Pluggable strategy for detecting entity semantic drift.

    Implementations analyse agent behaviour against canonical entity
    definitions and produce ``DriftReport`` instances quantifying
    divergence.
    """

    async def detect(
        self,
        entity_name: NotBlankStr,
        sample_agents: tuple[NotBlankStr, ...],
    ) -> DriftReport:
        """Run drift detection for a single entity.

        Args:
            entity_name: Entity to check.
            sample_agents: Agent IDs to sample.

        Returns:
            Drift report with divergence score and per-agent details.
        """
        ...

    @property
    def strategy_name(self) -> str:
        """Human-readable strategy identifier."""
        ...
