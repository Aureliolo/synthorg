"""Risk-tier-classifier factory (REWORK #9 + RFC#2).

Maps :class:`RiskClassifierType` to a concrete
:class:`RiskTierClassifier` via the ``StrEnum``-keyed
:class:`~synthorg.core.registry.StrategyRegistry`. ``DEFAULT`` is
byte-identical with the pre-plugin ``DefaultRiskTierClassifier()``;
non-default kinds whose required dependency is absent raise
:class:`RiskClassifierConfigError` at construction (fail fast).
"""

from typing import TYPE_CHECKING

from synthorg.core.registry import StrategyRegistry
from synthorg.security.timeout.errors import RiskClassifierConfigError
from synthorg.security.timeout.operator_configurable import (
    OperatorConfigurableRiskClassifier,
)
from synthorg.security.timeout.risk_classifier_config import (
    RiskClassifierConfig,
    RiskClassifierDeps,
    RiskClassifierType,
)
from synthorg.security.timeout.risk_tier_classifier import (
    DefaultRiskTierClassifier,
)
from synthorg.security.timeout.time_based_elevation import (
    TimeBasedRiskElevationClassifier,
)
from synthorg.security.timeout.workload_adaptive import (
    WorkloadAdaptiveRiskClassifier,
)

if TYPE_CHECKING:
    from synthorg.security.timeout.protocol import RiskTierClassifier


def _build_default(
    config: RiskClassifierConfig,
    _deps: RiskClassifierDeps,
) -> RiskTierClassifier:
    """Build the DEFAULT classifier (byte-identical with the static one).

    Returns:
        A ``DefaultRiskTierClassifier`` over the configured custom map.
    """
    return DefaultRiskTierClassifier(
        custom_map=dict(config.custom_map) or None,
    )


def _base_or_default(deps: RiskClassifierDeps) -> RiskTierClassifier:
    """Return the wrappers' base classifier, or a fresh default.

    Returns:
        ``deps.base`` when set, otherwise a new ``DefaultRiskTierClassifier``.
    """
    return deps.base if deps.base is not None else DefaultRiskTierClassifier()


def _build_workload_adaptive(
    config: RiskClassifierConfig,
    deps: RiskClassifierDeps,
) -> RiskTierClassifier:
    """Build the WORKLOAD_ADAPTIVE classifier wrapping the base.

    Returns:
        A ``WorkloadAdaptiveRiskClassifier``.

    Raises:
        RiskClassifierConfigError: If no ``inflight_probe`` was provided.
    """
    if deps.inflight_probe is None:
        msg = (
            "WORKLOAD_ADAPTIVE risk classifier requires an "
            "'inflight_probe' dependency but none was provided"
        )
        raise RiskClassifierConfigError(msg)
    return WorkloadAdaptiveRiskClassifier(
        base=_base_or_default(deps),
        inflight_probe=deps.inflight_probe,
        threshold=config.workload_threshold,
    )


def _build_operator_configurable(
    config: RiskClassifierConfig,
    _deps: RiskClassifierDeps,
) -> RiskTierClassifier:
    """Build the OPERATOR_CONFIGURABLE classifier from the operator map.

    Returns:
        An ``OperatorConfigurableRiskClassifier``.
    """
    return OperatorConfigurableRiskClassifier(
        operator_map=config.operator_map,
    )


def _build_time_based(
    config: RiskClassifierConfig,
    deps: RiskClassifierDeps,
) -> RiskTierClassifier:
    """Build the TIME_BASED elevation classifier wrapping the base.

    Returns:
        A ``TimeBasedRiskElevationClassifier`` over the configured
        off-hours window.
    """
    return TimeBasedRiskElevationClassifier(
        base=_base_or_default(deps),
        off_hours_start_hour=config.off_hours_start_hour,
        off_hours_end_hour=config.off_hours_end_hour,
        weekend_elevation=config.weekend_elevation,
        clock=deps.clock,
    )


_REGISTRY: StrategyRegistry[RiskTierClassifier] = StrategyRegistry(
    {
        RiskClassifierType.DEFAULT: _build_default,
        RiskClassifierType.WORKLOAD_ADAPTIVE: _build_workload_adaptive,
        RiskClassifierType.OPERATOR_CONFIGURABLE: _build_operator_configurable,
        RiskClassifierType.TIME_BASED: _build_time_based,
    },
    kind="risk_tier_classifier",
)


def build_risk_tier_classifier(
    config: RiskClassifierConfig,
    deps: RiskClassifierDeps,
) -> RiskTierClassifier:
    """Build the configured :class:`RiskTierClassifier`.

    Args:
        config: The classifier discriminator + per-impl tuning.
        deps: Runtime collaborators (in-flight probe, clock, base).

    Returns:
        A classifier satisfying the ``RiskTierClassifier`` protocol.
        ``config.kind == DEFAULT`` yields behaviour byte-identical
        with the pre-plugin static classifier.

    Raises:
        StrategyFactoryNotFoundError: Unknown ``config.kind``.
        RiskClassifierConfigError: A non-default kind is missing a
            required dependency.
    """
    return _REGISTRY.build(config.kind, config, deps)
