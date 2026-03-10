"""Factory for creating timeout policy instances from configuration."""

from ai_company.security.timeout.config import (
    ApprovalTimeoutConfig,
    DenyOnTimeoutConfig,
    EscalationChainConfig,
    TieredTimeoutConfig,
    WaitForeverConfig,
)
from ai_company.security.timeout.policies import (
    DenyOnTimeoutPolicy,
    EscalationChainPolicy,
    TieredTimeoutPolicy,
    WaitForeverPolicy,
)
from ai_company.security.timeout.protocol import TimeoutPolicy  # noqa: TC001
from ai_company.security.timeout.risk_tier_classifier import YamlRiskTierClassifier

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
            classifier=YamlRiskTierClassifier(),
        )

    if isinstance(config, EscalationChainConfig):
        return EscalationChainPolicy(
            chain=config.chain,
            on_chain_exhausted=config.on_chain_exhausted,
        )

    msg = f"Unknown timeout policy config type: {type(config).__name__}"  # type: ignore[unreachable]
    raise TypeError(msg)
