"""Strategic context providers.

Protocols and implementations for building the runtime
:class:`~synthorg.engine.strategy.models.StrategicContext` that shapes
how lenses and principles are applied to agent recommendations.
"""

import json
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, ValidationError

from synthorg.api.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.engine.strategy.models import StrategicContext, StrategyConfig
from synthorg.memory.models import MemoryQuery
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.strategy import (
    STRATEGY_CONTEXT_BUILT,
    STRATEGY_CONTEXT_MEMORY_QUERIED,
    STRATEGY_CONTEXT_PROVIDER_FAILED,
)

logger = get_logger(__name__)

_STRATEGIC_CONTEXT_AGENT_ID: NotBlankStr = NotBlankStr("system:strategy")
"""Synthetic agent id used for org-level strategic-context entries."""

_STRATEGIC_CONTEXT_TAG: NotBlankStr = NotBlankStr("strategic-context")
"""Tag the memory backend filters on for strategic-context entries."""


class _StrategicContextOverridesArgs(
    BaseModel
):  # lint-allow: frozen-extra-forbid -- extra="ignore" keeps this memory-backed typed boundary forward-compatible with future enrichment fields (class docstring)  # noqa: E501
    """Typed-boundary args model for memory-stored context overrides.

    The memory backend yields untrusted JSON; this args model is the
    boundary contract that turns that payload into typed overrides.
    Validated via :func:`synthorg.api.boundary.parse_typed` under the
    ``memory.strategic_context`` boundary label so failures emit the
    standard ``API_BOUNDARY_VALIDATION_FAILED`` log alongside the
    provider's own ``STRATEGY_CONTEXT_PROVIDER_FAILED`` log.

    Each override field is ``NotBlankStr`` so blank / non-string values
    reject the payload entirely; callers fall back to the no-override
    path on :class:`pydantic.ValidationError`.  ``extra="ignore"`` keeps
    the boundary forward-compatible with future enrichment fields.
    """

    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)

    maturity_stage: NotBlankStr | None = None
    industry: NotBlankStr | None = None
    competitive_position: NotBlankStr | None = None


# 2 impls (ConfigContextProvider, MemoryContextProvider) in this file;
# composition via fallback param.
@runtime_checkable
class StrategicContextProvider(Protocol):
    """Protocol for providing strategic context."""

    async def provide(self, *, config: StrategyConfig) -> StrategicContext:
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

    async def provide(self, *, config: StrategyConfig) -> StrategicContext:
        """Build context from config fields.

        Returns:
            A :class:`StrategicContext` populated from
            ``config.context``.
        """
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
    """Reads strategic context overrides from the memory backend.

    Queries org-level memory for the most recent ``strategic-context``
    entry tagged on the synthetic ``system:strategy`` agent, parses the
    entry content as a JSON object of overridable fields
    (``maturity_stage`` / ``industry`` / ``competitive_position``), and
    layers the overrides on top of the fallback provider's context.

    Falls back to the fallback provider when:

    - no memory backend was injected (degraded boot);
    - the backend retrieval call raises;
    - no strategic-context entries are stored;
    - the entry content is not parseable JSON.
    """

    def __init__(
        self,
        *,
        fallback: StrategicContextProvider,
        memory_backend: MemoryBackend | None = None,
    ) -> None:
        """Initialize with a fallback provider and an optional backend."""
        self._fallback = fallback
        self._memory_backend = memory_backend

    async def provide(self, *, config: StrategyConfig) -> StrategicContext:
        """Layer memory-stored overrides on top of the fallback context.

        Returns:
            A :class:`StrategicContext` whose fields are the
            fallback provider's values overridden by any well-formed
            memory entry; the unaltered fallback context when no
            backend is wired, no entries exist, or the entry fails
            JSON / schema validation.
        """
        if self._memory_backend is None:
            return await self._fallback.provide(config=config)

        try:
            entries = await self._memory_backend.retrieve(
                _STRATEGIC_CONTEXT_AGENT_ID,
                MemoryQuery(
                    categories=frozenset({MemoryCategory.SEMANTIC}),
                    tags=(_STRATEGIC_CONTEXT_TAG,),
                    limit=1,
                ),
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                STRATEGY_CONTEXT_PROVIDER_FAILED,
                provider_name="MemoryContextProvider",
                stage="retrieve",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return await self._fallback.provide(config=config)

        if not entries:
            logger.debug(
                STRATEGY_CONTEXT_MEMORY_QUERIED,
                outcome="no_entries",
            )
            return await self._fallback.provide(config=config)

        try:
            decoded = json.loads(entries[0].content)
        except json.JSONDecodeError as exc:
            logger.warning(
                STRATEGY_CONTEXT_PROVIDER_FAILED,
                provider_name="MemoryContextProvider",
                stage="parse",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return await self._fallback.provide(config=config)

        try:
            args = parse_typed(
                "memory.strategic_context",
                decoded,
                _StrategicContextOverridesArgs,
            )
        except ValidationError as exc:
            logger.warning(
                STRATEGY_CONTEXT_PROVIDER_FAILED,
                provider_name="MemoryContextProvider",
                stage="validate",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return await self._fallback.provide(config=config)

        overrides = args.model_dump(exclude_none=True)
        fallback_ctx = await self._fallback.provide(config=config)
        if not overrides:
            return fallback_ctx
        logger.info(
            STRATEGY_CONTEXT_MEMORY_QUERIED,
            outcome="overrides_applied",
            overridden_fields=sorted(overrides),
        )
        return fallback_ctx.model_copy(update=overrides)


class CompositeContextProvider:
    """Chains multiple context providers.

    Tries each provider in order and returns the first successful
    result.  This allows layered resolution: memory -> config.
    """

    def __init__(
        self,
        providers: tuple[StrategicContextProvider, ...],
    ) -> None:
        """Initialize with an ordered tuple of context providers.

        Raises:
            ValueError: When ``providers`` is empty (a composite chain
                needs at least one provider, typically the
                config-based fallback).
        """
        if not providers:
            msg = "CompositeContextProvider requires at least one provider"
            raise ValueError(msg)
        self._providers = providers

    async def provide(self, *, config: StrategyConfig) -> StrategicContext:
        """Try each provider in order, return first success.

        Returns:
            The first :class:`StrategicContext` produced by any
            provider in the chain.

        Raises:
            RuntimeError: When every provider raises a non-critical
                exception (the final fallback should make this
                unreachable in practice).
        """
        last_exc: Exception | None = None
        for i, provider in enumerate(self._providers):
            provider_name = type(provider).__name__
            try:
                return await provider.provide(config=config)
            except Exception as exc:
                reraise_critical(exc)
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


async def build_context(
    config: StrategyConfig,
    *,
    memory_backend: MemoryBackend | None = None,
) -> StrategicContext:
    """Convenience factory for building strategic context.

    Selects the appropriate provider based on ``config.context.source``
    and returns the resolved context.

    Args:
        config: Strategy configuration.
        memory_backend: Optional :class:`MemoryBackend` for memory-driven
            overrides. When ``None``, ``ContextSource.MEMORY`` and
            ``ContextSource.COMPOSITE`` degrade to pure config reads.

    Returns:
        Immutable strategic context snapshot.
    """
    from synthorg.engine.strategy.models import ContextSource  # noqa: PLC0415

    config_provider = ConfigContextProvider()

    if config.context.source == ContextSource.MEMORY:
        provider: StrategicContextProvider = MemoryContextProvider(
            fallback=config_provider,
            memory_backend=memory_backend,
        )
    elif config.context.source == ContextSource.COMPOSITE:
        # Scaffolding for future multi-provider chains (e.g. policy /
        # market-data overrides layered on top of memory).  Today the
        # tuple holds only ``MemoryContextProvider`` -- which already
        # falls back to ``ConfigContextProvider`` -- so the composite
        # wrapper is a no-op semantically.  Keep it so adding new
        # providers is a one-line tuple extension rather than a control
        # flow change.
        provider = CompositeContextProvider(
            providers=(
                MemoryContextProvider(
                    fallback=config_provider,
                    memory_backend=memory_backend,
                ),
            ),
        )
    else:
        provider = config_provider

    return await provider.provide(config=config)
