"""Strategic context providers.

Protocols and implementations for building the runtime
:class:`~synthorg.engine.strategy.models.StrategicContext` that shapes
how lenses and principles are applied to agent recommendations.
"""

import json
from typing import Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, ValidationError

from synthorg.core.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.engine.strategy.models import StrategicContext, StrategyConfig
from synthorg.memory.models import MemoryQuery
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.strategy import (
    STRATEGY_CONTEXT_BUILT,
    STRATEGY_CONTEXT_MEETING_QUERIED,
    STRATEGY_CONTEXT_MEMORY_QUERIED,
    STRATEGY_CONTEXT_PROVIDER_FAILED,
)

logger = get_logger(__name__)

#: Fraction of recent completed meetings reaching consensus (decisions made,
#: no conflicts) at or above which the org reads as internally aligned.
_HIGH_ALIGNMENT_RATIO: Final[float] = 0.6
#: Fraction at or below which recent meetings read as contested.
_LOW_ALIGNMENT_RATIO: Final[float] = 0.3

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
    Validated via :func:`synthorg.core.boundary.parse_typed` under the
    ``memory.strategic_context`` boundary label so failures emit the
    standard ``API_BOUNDARY_VALIDATION_FAILED`` log alongside the
    provider's own ``STRATEGY_CONTEXT_PROVIDER_FAILED`` log.

    Each override field is ``NotBlankStr`` so blank / non-string values
    reject the payload entirely; callers fall back to the no-override
    path on :class:`pydantic.ValidationError`.  ``extra="ignore"`` keeps
    the boundary forward-compatible with future enrichment fields.
    """

    model_config = ConfigDict(
        frozen=True, allow_inf_nan=False, extra="ignore", strict=True
    )

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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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


@runtime_checkable
class _MeetingMinutesView(Protocol):
    """Narrow read view of a completed meeting's minutes.

    Captures only the fields the meeting context provider inspects, so
    the strategy module never imports the communication meeting models.
    """

    @property
    def decisions(self) -> tuple[NotBlankStr, ...]: ...

    @property
    def conflicts_detected(self) -> bool: ...


@runtime_checkable
class _MeetingRecordView(Protocol):
    """Narrow read view of a meeting record (minutes set when completed)."""

    @property
    def minutes(self) -> _MeetingMinutesView | None: ...


@runtime_checkable
class MeetingRecordsSource(Protocol):
    """Source of recent meeting records (the meeting orchestrator).

    Structural so ``MeetingOrchestrator.get_records`` satisfies it without
    the strategy module importing the communication package.
    """

    def get_records(self) -> tuple[_MeetingRecordView, ...]:
        """Return meeting records, oldest-first.

        Returns:
            All recorded meetings in chronological order.
        """
        ...


class MeetingContextProvider:
    """Derives an internal-alignment qualifier from recent meetings.

    Inspects the most recent completed meetings (those with minutes) and
    measures how many reached consensus -- decisions recorded with no
    detected conflict. A high consensus ratio reads as an internally
    *aligned* org; a low ratio reads as *contested*. That signal is
    layered onto the fallback provider's ``competitive_position`` as a
    qualifier (e.g. ``challenger`` -> ``aligned challenger``), leaving the
    other context fields to the fallback.

    Falls back to the fallback provider when no orchestrator is wired or
    there are no completed meetings to inspect.
    """

    def __init__(
        self,
        *,
        fallback: StrategicContextProvider,
        records_source: MeetingRecordsSource | None = None,
        lookback: int,
    ) -> None:
        """Initialize with a fallback, a records source, and a lookback."""
        self._fallback = fallback
        self._records_source = records_source
        self._lookback = lookback

    async def provide(self, *, config: StrategyConfig) -> StrategicContext:
        """Layer a meeting-derived alignment qualifier on the fallback context.

        Returns:
            A :class:`StrategicContext` whose ``competitive_position`` is
            qualified by recent internal alignment, or the unaltered
            fallback context when no orchestrator is wired or no completed
            meetings exist.
        """
        fallback_ctx = await self._fallback.provide(config=config)
        if self._records_source is None:
            return fallback_ctx
        try:
            records = self._records_source.get_records()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                STRATEGY_CONTEXT_PROVIDER_FAILED,
                provider_name="MeetingContextProvider",
                stage="get_records",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return fallback_ctx

        completed = [r.minutes for r in records if r.minutes is not None]
        recent = completed[-self._lookback :]
        if not recent:
            logger.debug(STRATEGY_CONTEXT_MEETING_QUERIED, outcome="no_meetings")
            return fallback_ctx

        aligned = sum(1 for m in recent if m.decisions and not m.conflicts_detected)
        ratio = aligned / len(recent)
        qualifier = _alignment_qualifier(ratio)
        if qualifier is None:
            return fallback_ctx
        # Strip any qualifier a prior reload already prepended so repeated
        # refreshes do not compound ("aligned aligned challenger").
        base_position = _strip_alignment_qualifier(fallback_ctx.competitive_position)
        position = NotBlankStr(f"{qualifier} {base_position}")
        logger.info(
            STRATEGY_CONTEXT_MEETING_QUERIED,
            outcome="qualifier_applied",
            qualifier=qualifier,
            meetings=len(recent),
        )
        return fallback_ctx.model_copy(update={"competitive_position": position})


def _strip_alignment_qualifier(position: str) -> str:
    """Drop a leading ``aligned`` / ``contested`` qualifier if present.

    Returns:
        ``position`` without a single leading alignment-qualifier word, so a
        re-resolved context does not stack qualifiers across reloads.
    """
    for qualifier in ("aligned ", "contested "):
        if position.startswith(qualifier):
            return position[len(qualifier) :]
    return position


def _alignment_qualifier(ratio: float) -> str | None:
    """Map a consensus ratio to an alignment qualifier, or ``None``.

    Returns:
        ``"aligned"`` above the high threshold, ``"contested"`` at or
        below the low threshold, ``None`` in the indeterminate band.
    """
    if ratio >= _HIGH_ALIGNMENT_RATIO:
        return "aligned"
    if ratio <= _LOW_ALIGNMENT_RATIO:
        return "contested"
    return None


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
            ServiceUnavailableError: When every provider raises a
                non-critical exception (the final config fallback should
                make this unreachable in practice).
        """
        last_exc: Exception | None = None
        for i, provider in enumerate(self._providers):
            provider_name = type(provider).__name__
            try:
                return await provider.provide(config=config)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
        msg = "All strategic-context providers failed"
        raise ServiceUnavailableError(msg) from last_exc


async def build_context(
    config: StrategyConfig,
    *,
    memory_backend: MemoryBackend | None = None,
    meeting_records: MeetingRecordsSource | None = None,
) -> StrategicContext:
    """Convenience factory for building strategic context.

    Selects the appropriate provider based on ``config.context.source``
    and returns the resolved context.

    Args:
        config: Strategy configuration.
        memory_backend: Optional :class:`MemoryBackend` for memory-driven
            overrides. When ``None``, ``ContextSource.MEMORY`` and
            ``ContextSource.COMPOSITE`` degrade to pure config reads.
        meeting_records: Optional meeting-records source for the
            ``ContextSource.MEETING`` provider. When ``None`` that source
            degrades to a pure config read.

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
    elif config.context.source == ContextSource.MEETING:
        provider = MeetingContextProvider(
            fallback=config_provider,
            records_source=meeting_records,
            lookback=config.context.meeting_lookback,
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
