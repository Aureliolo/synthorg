"""Factory for sliding-window store strategies."""

from synthorg.api.rate_limits.config import PerOpRateLimitConfig  # noqa: TC001
from synthorg.api.rate_limits.in_memory import InMemorySlidingWindowStore
from synthorg.api.rate_limits.protocol import SlidingWindowStore  # noqa: TC001
from synthorg.core.registry import StrategyRegistry


def _build_memory(_config: PerOpRateLimitConfig) -> SlidingWindowStore:
    return InMemorySlidingWindowStore()


_REGISTRY: StrategyRegistry[SlidingWindowStore] = StrategyRegistry(
    {"memory": _build_memory},
    kind="rate_limit_window",
)


def build_sliding_window_store(
    config: PerOpRateLimitConfig,
) -> SlidingWindowStore:
    """Construct the configured :class:`SlidingWindowStore`.

    Args:
        config: Per-op rate limit configuration.

    Returns:
        A concrete :class:`SlidingWindowStore` implementation.

    Raises:
        StrategyFactoryNotFoundError: If ``config.backend`` is not registered.
    """
    return _REGISTRY.build(config.backend, config)
