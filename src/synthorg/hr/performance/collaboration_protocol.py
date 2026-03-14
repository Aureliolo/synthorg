"""Collaboration scoring strategy protocol.

Defines the interface for pluggable collaboration scoring strategies
that evaluate agent collaboration behavior (see Agents design page, D3).
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.hr.performance.models import (
    CollaborationMetricRecord,  # noqa: TC001
    CollaborationScoreResult,  # noqa: TC001
)


@runtime_checkable
class CollaborationScoringStrategy(Protocol):
    """Strategy for scoring agent collaboration behavior.

    Implementations evaluate behavioral telemetry records to produce
    a normalized collaboration score.
    """

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        ...

    async def score(
        self,
        *,
        agent_id: NotBlankStr,
        records: tuple[CollaborationMetricRecord, ...],
        role_weights: dict[str, float] | None = None,
    ) -> CollaborationScoreResult:
        """Score agent collaboration behavior.

        Args:
            agent_id: Agent being evaluated.
            records: Collaboration metric records to evaluate.
            role_weights: Optional per-component weight overrides.

        Returns:
            Collaboration score result with component scores.
        """
        ...
