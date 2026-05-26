"""Impact scoring for strategic decisions.

Scores decisions across 7 dimensions to determine the appropriate
level of strategic analysis (cost tier).  Pluggable behind the
:class:`ImpactScorer` protocol.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping

from synthorg.engine.strategy.models import (
    CostTierPreset,
    ImpactDimension,
    ImpactScore,
    ProgressiveConfig,
    RiskCard,
    StrategicContext,
)
from synthorg.observability import get_logger
from synthorg.observability.events.strategy import STRATEGY_IMPACT_SCORED

logger = get_logger(__name__)

# ── Dimension normalization maps ───────────────────────────────

_REVERSIBILITY_SCORES: Mapping[str, float] = MappingProxyType(
    {
        "easily_reversible": 0.2,
        "moderate": 0.5,
        "locked_in": 0.9,
    }
)

_BLAST_RADIUS_SCORES: Mapping[str, float] = MappingProxyType(
    {
        "individual": 0.1,
        "team": 0.3,
        "department": 0.6,
        "company_wide": 0.95,
    }
)

_TIME_HORIZON_SCORES: Mapping[str, float] = MappingProxyType(
    {
        "immediate": 0.1,
        "short_term": 0.3,
        "medium_term": 0.6,
        "long_term": 0.9,
    }
)


# 3 impls (CompositeImpactScorer, ExplicitImpactScorer, HybridImpactScorer)
# in this file.
@runtime_checkable
class ImpactScorer(Protocol):
    """Protocol for scoring the impact of a strategic decision."""

    def score(
        self,
        *,
        context: StrategicContext,
        risk_card: RiskCard,
        config: ProgressiveConfig,
    ) -> ImpactScore:
        """Score a decision's impact across all dimensions.

        Args:
            context: Strategic context for the evaluation.
            risk_card: Risk assessment of the decision.
            config: Progressive scoring configuration.

        Returns:
            Composite impact score with per-dimension breakdown.
        """
        ...


class CompositeImpactScorer:
    """Default 7-dimension weighted impact scorer.

    Maps :class:`RiskCard` fields to normalized dimension scores
    and computes a weighted composite using :class:`ProgressiveConfig`
    weights.
    """

    def score(
        self,
        *,
        context: StrategicContext,
        risk_card: RiskCard,
        config: ProgressiveConfig,
    ) -> ImpactScore:
        """Compute weighted composite score from risk card.

        Returns:
            An :class:`ImpactScore` carrying per-dimension scores, the
            clamped weighted composite, and the resolved cost tier.
        """
        dimensions = self._score_dimensions(context, risk_card)
        weights = config.weights.as_dict()

        composite = sum(
            dimensions.get(dim.value, 0.0) * weight for dim, weight in weights.items()
        )
        composite = max(0.0, min(1.0, composite))

        tier = _resolve_tier(composite, config)

        logger.debug(
            STRATEGY_IMPACT_SCORED,
            composite=composite,
            tier=tier,
            dimensions=dimensions,
        )

        return ImpactScore(
            dimensions=dimensions,
            composite=composite,
            tier=tier,
        )

    def _score_dimensions(
        self,
        context: StrategicContext,  # noqa: ARG002
        risk_card: RiskCard,
    ) -> dict[str, float]:
        """Normalize risk card fields into dimension scores.

        Returns:
            Mapping of dimension name to scalar score in ``[0, 1]``;
            unknown / unmapped values fall back to the mid-range
            defaults documented inline.
        """
        return {
            ImpactDimension.REVERSIBILITY.value: _REVERSIBILITY_SCORES.get(
                risk_card.reversibility.value, 0.5
            ),
            ImpactDimension.BLAST_RADIUS.value: _BLAST_RADIUS_SCORES.get(
                risk_card.blast_radius.value, 0.3
            ),
            ImpactDimension.TIME_HORIZON.value: _TIME_HORIZON_SCORES.get(
                risk_card.time_horizon.value, 0.5
            ),
            # Default mid-range for dimensions not directly on the card.
            ImpactDimension.BUDGET_IMPACT.value: 0.5,
            ImpactDimension.AUTHORITY_LEVEL.value: 0.5,
            ImpactDimension.DECISION_TYPE.value: 0.5,
            ImpactDimension.STRATEGIC_ALIGNMENT.value: 0.5,
        }


class ExplicitImpactScorer:
    """Accepts explicit per-dimension scores.

    Useful for testing or when the caller already knows the dimension
    values (e.g. from a form or API input).
    """

    def __init__(
        self,
        *,
        explicit_dimensions: dict[str, float],
    ) -> None:
        """Initialize with explicit per-dimension scores."""
        self._dimensions = dict(explicit_dimensions)

    def score(
        self,
        *,
        context: StrategicContext,  # noqa: ARG002
        risk_card: RiskCard,  # noqa: ARG002
        config: ProgressiveConfig,
    ) -> ImpactScore:
        """Compute composite from explicit dimension values.

        Returns:
            An :class:`ImpactScore` derived from the caller-supplied
            per-dimension scores.
        """
        weights = config.weights.as_dict()
        composite = sum(
            self._dimensions.get(dim.value, 0.0) * weight
            for dim, weight in weights.items()
        )
        composite = max(0.0, min(1.0, composite))
        tier = _resolve_tier(composite, config)

        return ImpactScore(
            dimensions=dict(self._dimensions),
            composite=composite,
            tier=tier,
        )


class HybridImpactScorer:
    """Combines explicit scoring with composite fallback.

    Uses explicit scores when available, falls back to the composite
    scorer for dimensions not provided explicitly.
    """

    def __init__(
        self,
        *,
        explicit_dimensions: dict[str, float] | None = None,
    ) -> None:
        """Initialize with optional explicit scores and composite fallback."""
        self._explicit = explicit_dimensions or {}
        self._composite = CompositeImpactScorer()

    def score(
        self,
        *,
        context: StrategicContext,
        risk_card: RiskCard,
        config: ProgressiveConfig,
    ) -> ImpactScore:
        """Merge explicit scores with composite fallback.

        Returns:
            An :class:`ImpactScore` where explicit per-dimension
            values override the composite scorer's defaults.
        """
        base = self._composite.score(
            context=context,
            risk_card=risk_card,
            config=config,
        )
        merged = dict(base.dimensions)
        merged.update(self._explicit)

        weights = config.weights.as_dict()
        composite = sum(
            merged.get(dim.value, 0.0) * weight for dim, weight in weights.items()
        )
        composite = max(0.0, min(1.0, composite))
        tier = _resolve_tier(composite, config)

        return ImpactScore(
            dimensions=merged,
            composite=composite,
            tier=tier,
        )


def _resolve_tier(
    composite: float,
    config: ProgressiveConfig,
) -> CostTierPreset:
    """Map composite score to cost tier using thresholds.

    Returns:
        The :class:`CostTierPreset` whose threshold band contains
        the ``composite`` score.
    """
    if composite < config.thresholds.moderate:
        return CostTierPreset.MINIMAL
    if composite < config.thresholds.generous:
        return CostTierPreset.MODERATE
    return CostTierPreset.GENEROUS
