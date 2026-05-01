"""Strategic context providers.

Protocols and implementations for building the runtime
:class:`~synthorg.engine.strategy.models.StrategicContext` that shapes
how lenses and principles are applied to agent recommendations.
"""

from typing import Protocol, runtime_checkable

from synthorg.engine.strategy.models import StrategicContext, StrategyConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.strategy import (
    STRATEGY_CONTEXT_BUILT,
    STRATEGY_CONTEXT_PROVIDER_FAILED,
)

logger = get_logger(__name__)


@runtime_checkable
class StrategicContextProvider(Protocol):
    """Protocol for providing strategic context."""

    def provide(self, *, config: StrategyConfig) -> StrategicContext:
        """Build strategic context from the given configuration.

        Args:
            config: Strategy configuration.

        Returns:
            Immutable strategic context snapshot.
        """
        ...


class ConfigContextProvider:
    """Reads strategic context directly from configuration.

    The simplest provider -- extracts maturity stage, industry, and
    competitive position from :class:`StrategyConfig.context`.
    """

    def provide(self, *, config: StrategyConfig) -> StrategicContext:
        """Build context from config fields."""
        ctx = StrategicContext(
            maturity_stage=config.context.maturity_stage,
            industry=config.context.industry,
            competitive_position=config.context.competitive_position,
        )
        logger.debug(
            STRATEGY_CONTEXT_BUILT,
            source="config",
            maturity_stage=ctx.maturity_stage,
            industry=ctx.industry,
            competitive_position=ctx.competitive_position,
        )
        return ctx


class MemoryContextProvider:
    """Reads strategic context from the memory system.

    Placeholder for Phase 2 integration.  Falls back to config-based
    context when memory data is unavailable.
    """

    def __init__(self, *, fallback: StrategicContextProvider) -> None:
        """Initialize with a fallback context provider."""
        self._fallback = fallback

    def provide(self, *, config: StrategyConfig) -> StrategicContext:
        """Build context from memory, falling back to config."""
        # Phase 2 will query agent/org memory for dynamic context.
        # For now, always delegate to the fallback provider.
        logger.debug(
            STRATEGY_CONTEXT_BUILT,
            source="memory_fallback",
        )
        return self._fallback.provide(config=config)


class CompositeContextProvider:
    """Chains multiple context providers.

    Tries each provider in order and returns the first successful
    result.  This allows layered resolution: memory -> config.
    """

    def __init__(
        self,
        providers: tuple[StrategicContextProvider, ...],
    ) -> None:
        """Initialize with an ordered tuple of context providers."""
        if not providers:
            msg = "CompositeContextProvider requires at least one provider"
            raise ValueError(msg)
        self._providers = providers

    def provide(self, *, config: StrategyConfig) -> StrategicContext:
        """Try each provider in order, return first success."""
        last_exc: Exception | None = None
        for i, provider in enumerate(self._providers):
            provider_name = type(provider).__name__
            try:
                return provider.provide(config=config)
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    STRATEGY_CONTEXT_PROVIDER_FAILED,
                    provider_index=i,
                    provider_name=provider_name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                last_exc = exc
                continue
        # Should not happen with ConfigContextProvider as final fallback.
        msg = "All context providers failed"
        raise RuntimeError(msg) from last_exc


def build_context(config: StrategyConfig) -> StrategicContext:
    """Convenience factory for building strategic context.

    Selects the appropriate provider based on ``config.context.source``
    and returns the resolved context.

    Args:
        config: Strategy configuration.

    Returns:
        Immutable strategic context snapshot.
    """
    from synthorg.engine.strategy.models import ContextSource  # noqa: PLC0415

    config_provider = ConfigContextProvider()

    if config.context.source == ContextSource.MEMORY:
        provider: StrategicContextProvider = MemoryContextProvider(
            fallback=config_provider,
        )
    elif config.context.source == ContextSource.COMPOSITE:
        provider = CompositeContextProvider(
            providers=(MemoryContextProvider(fallback=config_provider),),
        )
    else:
        provider = config_provider

    return provider.provide(config=config)
