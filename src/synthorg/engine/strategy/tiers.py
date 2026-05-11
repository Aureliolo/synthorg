"""Cost tier resolution for strategic analysis depth.

Determines the appropriate level of strategic analysis (minimal,
moderate, generous) based on decision impact scoring.
"""

from typing import Protocol, runtime_checkable

from synthorg.engine.strategy.models import (
    CostTierPreset,
    ImpactScore,
    StrategyConfig,
)
from synthorg.observability import get_logger
from synthorg.observability.events.strategy import STRATEGY_TIER_RESOLVED

logger = get_logger(__name__)


# audit-keep 2026-05-10: 2 impls (FixedTierResolver, ProgressiveTierResolver)
# + get_tier_resolver() factory in this file.
@runtime_checkable
class CostTierResolver(Protocol):
    """Protocol for resolving cost tiers."""

    def resolve(
        self,
        *,
        impact: ImpactScore | None,
        config: StrategyConfig,
    ) -> CostTierPreset:
        """Resolve the cost tier for a strategic decision.

        Args:
            impact: Impact score (None for fixed resolution).
            config: Strategy configuration.

        Returns:
            Resolved cost tier preset.
        """
        ...


class FixedTierResolver:
    """Always returns the configured default cost tier."""

    def resolve(
        self,
        *,
        impact: ImpactScore | None,  # noqa: ARG002
        config: StrategyConfig,
    ) -> CostTierPreset:
        """Return the default tier from config."""
        tier = config.cost_tier
        logger.debug(
            STRATEGY_TIER_RESOLVED,
            resolver="fixed",
            tier=tier,
        )
        return tier


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
        """Resolve tier from impact score thresholds."""
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


def get_tier_resolver(config: StrategyConfig) -> CostTierResolver:  # noqa: ARG001
    """Factory for cost tier resolvers.

    Currently always returns :class:`ProgressiveTierResolver`, which
    falls back to the config's default tier when no impact score is
    available (covering the ``FixedTierResolver`` use case).

    Args:
        config: Strategy configuration (reserved for future
            resolver selection logic).

    Returns:
        A :class:`ProgressiveTierResolver` instance.
    """
    return ProgressiveTierResolver()
