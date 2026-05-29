# module-kind: code
"""Feature-owned service start/stop dispatcher for the composition root.

Drives the :class:`~synthorg._core.features.ServiceLifecycleHook`
contributions a feature declares. The composition root collects every
discovered feature's hooks (already in dependency order) and hands them to
:class:`FeatureLifecycleRunner`, which:

- starts the hooks in order, tracking which started;
- on a hook whose ``fatal_on_start_error`` is set, rolls back the
  already-started hooks (reverse order) and re-raises so boot fails fast;
- on a non-fatal start failure, logs and continues (best-effort wiring);
- on shutdown, stops only the started hooks in REVERSE order under each
  hook's ``stop_timeout_seconds``.

Shutdown reuses the core lifecycle's :func:`_try_stop`, so the
``MemoryError`` / ``RecursionError`` re-raise, the redacted
``TimeoutError`` handling, and the log-and-swallow-everything-else
behaviour match the hand-written core-scaffold teardown exactly: a hook
that hangs past its budget never wedges the shutdown window.
"""

import asyncio
from collections.abc import Sequence

from synthorg._core.features import ServiceLifecycleHook
from synthorg.api.lifecycle import _try_stop
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_SHUTDOWN, API_APP_STARTUP

logger = get_logger(__name__)


class FeatureLifecycleRunner:
    """Starts feature service hooks; tears down the started ones in reverse.

    A single runner instance owns one start/stop cycle: ``start_all`` records
    every hook that started so ``stop_all`` (or the rollback on a fatal start
    failure) tears down exactly those, newest first.
    """

    def __init__(self, hooks: Sequence[ServiceLifecycleHook]) -> None:
        """Store the dependency-ordered hooks for this start/stop cycle.

        Args:
            hooks: The feature service hooks in dependency order (the order
                ``discover_features`` resolved).
        """
        self._hooks: tuple[ServiceLifecycleHook, ...] = tuple(hooks)
        self._started: list[ServiceLifecycleHook] = []

    async def start_all(self) -> None:
        """Start each hook in order, rolling back on a fatal start failure.

        Raises:
            MemoryError: Propagated unchanged (never swallowed).
            RecursionError: Propagated unchanged (never swallowed).
            Exception: A fatal hook's ``start`` failure, re-raised after
                rolling back the already-started hooks in reverse order.
        """
        for hook in self._hooks:
            try:
                awaitable = hook.start()
                if hook.start_timeout_seconds is not None:
                    await asyncio.wait_for(
                        awaitable, timeout=hook.start_timeout_seconds
                    )
                else:
                    await awaitable
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                if hook.fatal_on_start_error:
                    logger.error(
                        API_APP_STARTUP,
                        action="feature_hook_start_failed_fatal",
                        service=hook.name,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    await self.stop_all()
                    raise
                logger.warning(
                    API_APP_STARTUP,
                    action="feature_hook_start_failed",
                    service=hook.name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                continue
            self._started.append(hook)

    async def stop_all(self) -> None:
        """Stop the started hooks in reverse order under each stop budget."""
        for hook in reversed(self._started):
            await _try_stop(
                hook.stop(),
                API_APP_SHUTDOWN,
                f"stopping feature hook {hook.name}",
                timeout=hook.stop_timeout_seconds,
                service=hook.name,
            )
        self._started.clear()
