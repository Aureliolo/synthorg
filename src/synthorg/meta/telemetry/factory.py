"""Factory functions for cross-deployment analytics components.

Constructs emitters, collectors, and recommenders from
configuration, following the protocol + strategy + factory
pattern used throughout the meta subsystem.
"""

from collections.abc import Collection

from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.telemetry.collector import InMemoryAnalyticsCollector
from synthorg.meta.telemetry.emitter import HttpAnalyticsEmitter
from synthorg.meta.telemetry.recommender import DefaultThresholdRecommender
from synthorg.observability import get_logger

logger = get_logger(__name__)


def build_analytics_emitter(
    config: SelfImprovementConfig,
    *,
    builtin_rule_names: Collection[str],
) -> HttpAnalyticsEmitter | None:
    """Build an analytics emitter from config.

    Returns ``None`` if cross-deployment analytics is disabled.

    Args:
        config: Self-improvement configuration.
        builtin_rule_names: Set of built-in rule names for
            anonymization.

    Returns:
        Configured emitter or None.
    """
    analytics = config.cross_deployment_analytics
    if not analytics.enabled:
        return None
    return HttpAnalyticsEmitter(
        analytics_config=analytics,
        self_improvement_config=config,
        builtin_rule_names=builtin_rule_names,
    )


def build_analytics_collector(
    config: SelfImprovementConfig,
) -> InMemoryAnalyticsCollector | None:
    """Build an analytics collector from config.

    Returns ``None`` if the collector role is not enabled or
    if the master analytics switch is off.

    Args:
        config: Self-improvement configuration.

    Returns:
        Configured collector or None.
    """
    analytics = config.cross_deployment_analytics
    if not analytics.enabled or not analytics.collector_enabled:
        return None
    return InMemoryAnalyticsCollector()


def build_recommender(
    config: SelfImprovementConfig,
) -> DefaultThresholdRecommender:
    """Build a threshold recommender from config.

    Args:
        config: Self-improvement configuration.

    Returns:
        Configured recommender instance.
    """
    analytics = config.cross_deployment_analytics
    return DefaultThresholdRecommender(
        min_deployments=analytics.min_deployments_for_pattern,
        min_observations=analytics.recommendation_min_observations,
    )
