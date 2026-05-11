"""Pillar scoring strategy protocol.

Defines the interface for pluggable pillar scoring strategies
that evaluate agent performance on a single evaluation pillar.
"""

from typing import Protocol, runtime_checkable

from synthorg.hr.evaluation.enums import EvaluationPillar  # noqa: TC001
from synthorg.hr.evaluation.models import (
    EvaluationContext,  # noqa: TC001
    PillarScore,  # noqa: TC001
)


# 5 strategy slots in EvaluationService (intelligence/efficiency/resilience/
# governance/ux) + ConfigurablePillarScorer default impl.
@runtime_checkable
class PillarScoringStrategy(Protocol):
    """Strategy for scoring a single evaluation pillar.

    Implementations read the fields they need from the
    ``EvaluationContext`` and return a ``PillarScore``.
    """

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        ...

    @property
    def pillar(self) -> EvaluationPillar:
        """Which pillar this strategy scores."""
        ...

    async def score(self, *, context: EvaluationContext) -> PillarScore:
        """Score the pillar from the evaluation context.

        Args:
            context: Evaluation context with all available data.

        Returns:
            Pillar score with breakdown and confidence.
        """
        ...
