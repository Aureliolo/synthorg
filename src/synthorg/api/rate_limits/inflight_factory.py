"""Factory for per-operation inflight-store strategies."""

from synthorg.api.rate_limits.in_memory_inflight import InMemoryInflightStore
from synthorg.api.rate_limits.inflight_config import (
    PerOpConcurrencyConfig,  # noqa: TC001
)
from synthorg.api.rate_limits.inflight_protocol import InflightStore  # noqa: TC001
from synthorg.core.registry import StrategyRegistry


def _build_memory(_config: PerOpConcurrencyConfig) -> InflightStore:
    return InMemoryInflightStore()


_REGISTRY: StrategyRegistry[InflightStore] = StrategyRegistry(
    {"memory": _build_memory},
    kind="inflight_store",
)


def build_inflight_store(config: PerOpConcurrencyConfig) -> InflightStore:
    """Construct the configured :class:`InflightStore`.

    Args:
        config: Per-op concurrency configuration.

    Returns:
        A concrete :class:`InflightStore` implementation.

    Raises:
        StrategyFactoryNotFoundError: If ``config.backend`` is not registered.
    """
    return _REGISTRY.build(config.backend, config)
