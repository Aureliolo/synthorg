"""Factory for sliding-window store strategies."""

from synthorg.api.rate_limits.config import PerOpRateLimitConfig  # noqa: TC001
from synthorg.api.rate_limits.in_memory import InMemorySlidingWindowStore
from synthorg.api.rate_limits.protocol import SlidingWindowStore  # noqa: TC001
from synthorg.core.registry import (
    StrategyFactoryNotFoundError,
    StrategyRegistry,
)
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_RATE_LIMIT_BACKEND_UNSUPPORTED

logger = get_logger(__name__)


def _build_memory(_config: PerOpRateLimitConfig) -> SlidingWindowStore:
    """Build the memory.

    Returns:
        ``SlidingWindowStore`` instance.
    """
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
    try:
        return _REGISTRY.build(config.backend, config)
    except StrategyFactoryNotFoundError:
        logger.warning(
            API_RATE_LIMIT_BACKEND_UNSUPPORTED,
            backend=config.backend,
            available=_REGISTRY.names(),
        )
        raise
