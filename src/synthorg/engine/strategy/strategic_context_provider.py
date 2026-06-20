# module-kind: adapter
"""Cached snapshot of the resolved strategic context for the prompt path.

The prompt-build read path (``build_strategic_prompt_sections``) is
synchronous, but resolving strategic context can be async (memory reads,
meeting orchestrator reads via :func:`build_context`). This provider
bridges the gap: it resolves a :class:`StrategicContext` snapshot via an
injected async resolver, caches it, and serves a synchronous read. The
boot wiring refreshes the snapshot after the engine is built and on a
strategy settings hot-reload, so the prompt path stays await-free while
still reflecting the configured ``ContextSource`` (config / memory /
meeting / composite).

Strategic context is slow-changing, organisation-wide state, so a cached
snapshot refreshed at boot / reload is the right granularity -- the same
trade-off the active-principle ambient provider makes.
"""

from collections.abc import Awaitable, Callable

from synthorg.engine.strategy.models import StrategicContext
from synthorg.observability import get_logger
from synthorg.observability.events.strategy import (
    STRATEGY_CONTEXT_SNAPSHOT_REFRESHED,
)

logger = get_logger(__name__)

#: Async resolver returning the freshly-built strategic context snapshot.
StrategicContextResolver = Callable[[], Awaitable[StrategicContext]]


class CachedStrategicContextProvider:
    """In-memory cached snapshot of the resolved strategic context.

    Args:
        resolver: Async callable that resolves a fresh snapshot. Built at
            the wiring layer from :func:`build_context` bound to the live
            config, memory backend, and meeting orchestrator.
    """

    def __init__(self, *, resolver: StrategicContextResolver) -> None:
        self._resolver = resolver
        self._snapshot: StrategicContext | None = None

    async def refresh(self) -> None:
        """Re-resolve and cache the strategic context snapshot."""
        self._snapshot = await self._resolver()
        logger.info(
            STRATEGY_CONTEXT_SNAPSHOT_REFRESHED,
            maturity_stage=self._snapshot.maturity_stage,
            industry=self._snapshot.industry,
            competitive_position=self._snapshot.competitive_position,
        )

    def current(self) -> StrategicContext | None:
        """Return the cached snapshot, or ``None`` before the first refresh.

        Returns:
            The cached strategic context, or ``None``.
        """
        return self._snapshot


#: Process-global ambient strategic-context provider for the synchronous
#: prompt-build path. Set once at boot (after the engine is built) and
#: process-wide because strategic context is organisation-wide policy in a
#: single-company deployment. A module global -- not a ``ContextVar`` --
#: so the value is visible across every request coroutine and the
#: ``asyncio.to_thread`` render worker, mirroring the active-principle
#: ambient provider.
_AMBIENT_PROVIDER: CachedStrategicContextProvider | None = None


def set_strategic_context_provider(
    provider: CachedStrategicContextProvider | None,
) -> None:
    """Set the process-global ambient strategic-context provider.

    Tests reset to ``None`` to restore isolation.
    """
    global _AMBIENT_PROVIDER  # noqa: PLW0603 -- single process-wide org policy
    _AMBIENT_PROVIDER = provider


def current_strategic_context() -> StrategicContext | None:
    """Return the ambient resolved strategic context, or ``None`` when unset.

    Returns:
        The cached strategic context snapshot, or ``None`` when no provider
        is bound or it has not been refreshed yet.
    """
    if _AMBIENT_PROVIDER is None:
        return None
    return _AMBIENT_PROVIDER.current()


__all__ = [
    "CachedStrategicContextProvider",
    "StrategicContextResolver",
    "current_strategic_context",
    "set_strategic_context_provider",
]
