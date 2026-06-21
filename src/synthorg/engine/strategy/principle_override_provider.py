# module-kind: adapter
"""Cached snapshot provider for the durable principle-override store.

The rollback executor's ``PromptMutator`` persists restored principle text as
override rows keyed by ``scope`` (the principle id from the YAML packs). The
prompt-build read path (``load_and_merge`` -> ``inject_strategy_context``) is
synchronous, but the override store is async. This provider bridges the gap:
it loads a scope->text snapshot from the store via an injected async loader
(built at the wiring layer so the engine never imports a persistence concrete),
caches it, and serves synchronous reads. The mutator calls :meth:`refresh`
after a successful write so the next prompt build overlays the new text without
a restart.

Overrides are low-cardinality org policy, so an in-memory snapshot is cheaper
than a per-build durable query and keeps the read path await-free.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from synthorg.observability import get_logger
from synthorg.observability.events.strategy import (
    STRATEGY_PRINCIPLE_OVERRIDE_SNAPSHOT_REFRESHED,
)

logger = get_logger(__name__)

#: Async loader returning the full scope->text override snapshot.
PrincipleOverrideLoader = Callable[[], Awaitable[Mapping[str, str]]]


@runtime_checkable
class PrincipleOverrideProvider(Protocol):
    """Synchronous read seam over the durable principle-override snapshot.

    Implemented by a cached provider that refreshes from the async repository
    at boot and on mutator writes, so the synchronous prompt-build path can
    overlay override text without an await.
    """

    def overrides(self) -> Mapping[str, str]:
        """Return the current scope->text override map (read-only)."""
        ...


class CachedPrincipleOverrideProvider:
    """In-memory cached scope->text snapshot over the durable override store.

    Args:
        loader: Async callable returning the full scope->text snapshot. Built
            at the wiring layer from the durable repository.
    """

    def __init__(self, *, loader: PrincipleOverrideLoader) -> None:
        self._loader = loader
        self._snapshot: Mapping[str, str] = MappingProxyType({})
        self._refresh_lock = asyncio.Lock()

    async def refresh(self) -> None:
        """Reload the scope->text snapshot from the durable store.

        Called at boot and by the mutator after a successful write. Serialised
        so two concurrent refreshes cannot interleave their load and assignment
        and leave the cache holding the older loader result.
        """
        async with self._refresh_lock:
            snapshot = MappingProxyType(dict(await self._loader()))
            self._snapshot = snapshot
        logger.info(
            STRATEGY_PRINCIPLE_OVERRIDE_SNAPSHOT_REFRESHED,
            count=len(snapshot),
        )

    def overrides(self) -> Mapping[str, str]:
        """Return the current scope->text override map.

        Returns:
            A read-only mapping from principle id (scope) to override text.
        """
        return self._snapshot


#: Process-global ambient principle-override provider for the synchronous
#: prompt-build path. Mirrors ``_AMBIENT_PROVIDER`` in
#: ``active_principle_provider``: a module global (not a ``ContextVar``) so the
#: value is visible across every request coroutine and the ``asyncio.to_thread``
#: worker, not only the boot context that would own a contextvar ``set``. Set
#: once at boot (after persistence connects) and process-wide because overrides
#: are organisation-wide policy in a single-company deployment.
_AMBIENT_OVERRIDE_PROVIDER: PrincipleOverrideProvider | None = None


def set_principle_override_provider(
    provider: PrincipleOverrideProvider | None,
) -> None:
    """Set the process-global ambient principle-override provider.

    Tests reset to ``None`` to restore isolation.
    """
    global _AMBIENT_OVERRIDE_PROVIDER  # noqa: PLW0603 -- single process-wide org policy
    _AMBIENT_OVERRIDE_PROVIDER = provider


def current_principle_override_provider() -> PrincipleOverrideProvider | None:
    """Return the ambient principle-override provider, or ``None`` when unset.

    Returns:
        The bound provider, or ``None``.
    """
    return _AMBIENT_OVERRIDE_PROVIDER


__all__ = [
    "CachedPrincipleOverrideProvider",
    "PrincipleOverrideLoader",
    "PrincipleOverrideProvider",
    "current_principle_override_provider",
    "set_principle_override_provider",
]
