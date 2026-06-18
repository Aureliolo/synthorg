"""Cost tier resolution for strategic analysis depth.

Determines the appropriate level of strategic analysis (minimal,
moderate, generous) based on decision impact scoring.
"""

from synthorg.engine.strategy.models import (
    CostTierPreset,
    ImpactScore,
    StrategyConfig,
)
from synthorg.observability import get_logger
from synthorg.observability.events.strategy import STRATEGY_TIER_RESOLVED

logger = get_logger(__name__)


class ProgressiveTierResolver:
    """Resolves tier based on impact score and thresholds.

    Uses the composite impact score against configured thresholds:
    - Below ``moderate`` threshold -> minimal
    - Between ``moderate`` and ``generous`` -> moderate
    - At or above ``generous`` threshold -> generous

    Falls back to the configured default tier when no impact score
    is available.
    """

    def resolve(
        self,
        *,
        impact: ImpactScore | None,
        config: StrategyConfig,
    ) -> CostTierPreset:
        """Resolve tier from impact score thresholds.

        Returns:
            The :class:`CostTierPreset` selected from the composite
            score's threshold band; the config's default tier when
            no impact score is supplied.
        """
        if impact is None:
            tier = config.cost_tier
            logger.debug(
                STRATEGY_TIER_RESOLVED,
                resolver="progressive_fallback",
                tier=tier,
            )
            return tier

        thresholds = config.progressive.thresholds
        if impact.composite < thresholds.moderate:
            tier = CostTierPreset.MINIMAL
        elif impact.composite < thresholds.generous:
            tier = CostTierPreset.MODERATE
        else:
            tier = CostTierPreset.GENEROUS

        logger.debug(
            STRATEGY_TIER_RESOLVED,
            resolver="progressive",
            composite=impact.composite,
            tier=tier,
        )
        return tier
