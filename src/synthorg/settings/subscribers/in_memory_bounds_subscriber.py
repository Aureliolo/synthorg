"""In-memory bound settings subscriber.

Rebounds the in-memory buffers an operator can size at runtime: the
coordination-metrics store (``budget.coordination_metrics_max_entries``), the
single-agent baseline window (``budget.baseline_window_size``) and the task
engine's mutation admission cap (``engine.task_engine_max_queue_size``).

Nothing in flight is discarded: the ring buffers are rebuilt keeping their
newest records, so raising a bound costs no history and lowering one drops
exactly what the next writes would have evicted; the admission cap governs only
what is accepted next, so lowering it lets the backlog drain rather than
dropping accepted mutations.
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.coordination.state import CoordinationStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.state import EngineStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.service import SettingsService
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

_COORDINATION_METRICS = (
    SettingNamespace.BUDGET.value,
    "coordination_metrics_max_entries",
)
_BASELINE_WINDOW = (SettingNamespace.BUDGET.value, "baseline_window_size")
_TASK_ENGINE_QUEUE = (
    SettingNamespace.ENGINE.value,
    "task_engine_max_queue_size",
)
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        _COORDINATION_METRICS,
        _BASELINE_WINDOW,
        _TASK_ENGINE_QUEUE,
    }
)


class InMemoryBoundsSettingsSubscriber:
    """Apply bound changes onto the live in-memory buffers.

    Args:
        app_state: Application state owning the buffers and the resolver.
        settings_service: Held for symmetry with peer subscribers.
    """

    def __init__(
        self,
        app_state: AppState,
        settings_service: SettingsService,
    ) -> None:
        self._app_state = app_state
        self._settings_service = settings_service

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return the ``(namespace, key)`` pairs this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logs."""
        return "in-memory-bounds"

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Apply each changed bound to its matching buffer.

        Args:
            changes: The watched writes to apply. Each names a different
                buffer, so the batch is applied one key at a time.
        """
        for namespace, key in changes:
            await self._apply(namespace, key)

    async def _apply(self, namespace: str, key: str) -> None:
        """Resolve the new bound and apply it to the matching buffer.

        Raises:
            Exception: Re-raised after logging so the dispatcher records the
                failure with subscriber context.
        """
        if (namespace, key) not in _WATCHED:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected pair",
            )
            return
        try:
            value = await config_resolver_of(self._app_state).get_int(namespace, key)
            if (namespace, key) == _COORDINATION_METRICS:
                self._rebound_coordination_metrics(value)
            elif (namespace, key) == _BASELINE_WINDOW:
                self._rebound_baseline_window(value)
            else:
                self._rebound_task_engine_queue(value)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="in_memory_bounds",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            SETTINGS_SUBSCRIBER_NOTIFIED,
            subscriber=self.subscriber_name,
            namespace=namespace,
            key=key,
            note="bound applied to the live buffer",
        )

    def _rebound_coordination_metrics(self, value: int) -> None:
        """Rebound the coordination-metrics store when it is wired."""
        store = self._app_state.slice(CoordinationStateSlice).metrics_store
        if store is not None:
            store.set_max_entries(value)

    def _rebound_baseline_window(self, value: int) -> None:
        """Rebound the single-agent baseline window when it is wired."""
        store = self._app_state.slice(CoordinationStateSlice).baseline_store
        if store is not None:
            store.set_window_size(value)

    def _rebound_task_engine_queue(self, value: int) -> None:
        """Rebound the task engine's mutation admission cap when it is wired."""
        engine = self._app_state.slice(EngineStateSlice).task_engine
        if engine is not None:
            engine.set_max_queue_size(value)
