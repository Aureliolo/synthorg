# module-kind: adapter
"""Cached snapshot provider for the durable active-principle store.

The prompt-build read path (``load_and_merge`` -> ``inject_strategy_context``)
is synchronous, but the durable store is async. This provider bridges the gap:
it loads a full snapshot from the store via an injected async loader (built at
the wiring layer so the engine never imports a persistence concrete), caches it,
and serves synchronous scope-filtered reads. The meta-loop applier calls
:meth:`refresh` after a successful write so the next prompt build sees the new
principle without a restart.

Active principles are low-cardinality org policy, so an in-memory snapshot
filtered per request is cheaper than a per-agent durable query and keeps the
read path await-free.
"""

import asyncio
from collections.abc import Awaitable, Callable

from synthorg.engine.strategy.active_principle import (
    ActivePrinciple,
    ActivePrincipleProvider,
    ScopeKind,
)
from synthorg.observability import get_logger
from synthorg.observability.events.strategy import (
    STRATEGY_ACTIVE_PRINCIPLE_SNAPSHOT_REFRESHED,
)

logger = get_logger(__name__)

#: Async loader returning the full active-principle snapshot.
ActivePrincipleLoader = Callable[[], Awaitable[tuple[ActivePrinciple, ...]]]


def _normalise(value: str | None) -> str | None:
    """Lower-case and strip a scope name for case-insensitive matching.

    Returns:
        The normalised name, or ``None`` when *value* is ``None``.
    """
    return None if value is None else value.strip().casefold()


def _in_scope(
    principle: ActivePrinciple,
    *,
    role_key: str | None,
    dept_key: str | None,
) -> bool:
    """Return whether *principle* applies to an agent's role / department.

    Returns:
        ``True`` for an ``ALL``-scoped principle, a ``ROLE``-scoped principle
        matching ``role_key``, or a ``DEPARTMENT``-scoped principle matching
        ``dept_key``.
    """
    if principle.scope_kind is ScopeKind.ALL:
        return True
    scope_key = _normalise(principle.scope)
    if principle.scope_kind is ScopeKind.ROLE:
        return role_key is not None and scope_key == role_key
    return dept_key is not None and scope_key == dept_key


class CachedActivePrincipleProvider:
    """In-memory cached snapshot over the durable active-principle store.

    Args:
        loader: Async callable returning the full snapshot. Built at the
            wiring layer from the durable repository.
    """

    def __init__(self, *, loader: ActivePrincipleLoader) -> None:
        self._loader = loader
        self._snapshot: tuple[ActivePrinciple, ...] = ()
        self._refresh_lock = asyncio.Lock()

    async def refresh(self) -> None:
        """Reload the snapshot from the durable store.

        Called at boot and by the prompt applier after a successful write.
        Serialised so two concurrent refreshes cannot interleave their load
        and assignment and leave the cache holding the older loader result.
        """
        async with self._refresh_lock:
            snapshot = await self._loader()
            self._snapshot = snapshot
        logger.info(
            STRATEGY_ACTIVE_PRINCIPLE_SNAPSHOT_REFRESHED,
            count=len(snapshot),
        )

    def list_active(
        self,
        *,
        role: str | None,
        department: str | None,
    ) -> tuple[ActivePrinciple, ...]:
        """Return active principles in scope for an agent.

        Includes every ``ALL``-scoped principle, plus ``ROLE``-scoped
        principles whose ``scope`` matches ``role`` and ``DEPARTMENT``-scoped
        principles whose ``scope`` matches ``department`` (case-insensitive).

        Returns:
            The in-scope principles, in snapshot (newest-first) order.
        """
        role_key = _normalise(role)
        dept_key = _normalise(department)
        return tuple(
            principle
            for principle in self._snapshot
            if _in_scope(principle, role_key=role_key, dept_key=dept_key)
        )

    def snapshot(self) -> tuple[ActivePrinciple, ...]:
        """Return the full cached snapshot (for the applier's scope reads).

        Returns:
            Every cached active principle, newest-first.
        """
        return self._snapshot


#: Process-global ambient active-principle provider for the synchronous
#: prompt-build path. The deep render call graph (``build_system_prompt`` ->
#: trim loop -> ``inject_strategy_context``) reads this without threading a
#: provider through every signature. It is set once at boot (after persistence
#: connects, by the meta-apply wiring) and is process-wide because active
#: principles are organisation-wide policy in a single-company deployment. A
#: module global -- not a ``ContextVar`` -- because the value must be visible
#: across every request coroutine and the ``asyncio.to_thread`` worker, not
#: only the boot context that would own a contextvar ``set``.
_AMBIENT_PROVIDER: ActivePrincipleProvider | None = None


def set_active_principle_provider(provider: ActivePrincipleProvider | None) -> None:
    """Set the process-global ambient active-principle provider.

    Tests reset to ``None`` to restore isolation.
    """
    global _AMBIENT_PROVIDER  # noqa: PLW0603 -- single process-wide org policy
    _AMBIENT_PROVIDER = provider


def current_active_principle_provider() -> ActivePrincipleProvider | None:
    """Return the ambient active-principle provider, or ``None`` when unset.

    Returns:
        The bound provider, or ``None``.
    """
    return _AMBIENT_PROVIDER


__all__ = [
    "ActivePrincipleLoader",
    "CachedActivePrincipleProvider",
    "current_active_principle_provider",
    "set_active_principle_provider",
]
