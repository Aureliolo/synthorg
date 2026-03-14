"""Factory for creating timeout policy instances from configuration."""

from synthorg.observability import get_logger
from synthorg.observability.events.timeout import TIMEOUT_FACTORY_UNKNOWN_CONFIG
from synthorg.security.timeout.config import (
    ApprovalTimeoutConfig,
    DenyOnTimeoutConfig,
    EscalationChainConfig,
    TieredTimeoutConfig,
    WaitForeverConfig,
)
from synthorg.security.timeout.policies import (
    DenyOnTimeoutPolicy,
    EscalationChainPolicy,
    TieredTimeoutPolicy,
    WaitForeverPolicy,
)
from synthorg.security.timeout.protocol import TimeoutPolicy  # noqa: TC001
from synthorg.security.timeout.risk_tier_classifier import DefaultRiskTierClassifier

logger = get_logger(__name__)

_SECONDS_PER_MINUTE = 60.0


def create_timeout_policy(
    config: ApprovalTimeoutConfig,
) -> TimeoutPolicy:
    """Create a timeout policy from its configuration.

    Args:
        config: One of the four timeout policy configurations.

    Returns:
        A configured timeout policy instance.

    Raises:
        TypeError: If config type is not recognized.
    """
    if isinstance(config, WaitForeverConfig):
        return WaitForeverPolicy()

    if isinstance(config, DenyOnTimeoutConfig):
        return DenyOnTimeoutPolicy(
            timeout_seconds=config.timeout_minutes * _SECONDS_PER_MINUTE,
        )

    if isinstance(config, TieredTimeoutConfig):
        return TieredTimeoutPolicy(
            tiers=config.tiers,
            classifier=DefaultRiskTierClassifier(),
        )

    if isinstance(config, EscalationChainConfig):
        return EscalationChainPolicy(
            chain=config.chain,
            on_chain_exhausted=config.on_chain_exhausted,
        )

    msg = f"Unknown timeout policy config type: {type(config).__name__}"  # type: ignore[unreachable]
    logger.warning(
        TIMEOUT_FACTORY_UNKNOWN_CONFIG,
        config_type=type(config).__name__,
    )
    raise TypeError(msg)
