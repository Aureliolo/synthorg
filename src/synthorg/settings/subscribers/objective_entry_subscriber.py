"""Objective-entry settings subscriber.

Re-wires the real objective work-entry adapter when an operator edits
``objectives.default_project``. ``wire_real_objective_entry`` resolves the slug
through the live settings chain and hot-swaps the adapter so the new project
stamps every subsequently-filed objective WorkItem without a restart.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {("objectives", "default_project")},
)


class ObjectiveEntrySettingsSubscriber:
    """Re-wire the objective entry adapter on ``objectives.default_project``.

    Args:
        app_state: Application state carrying the work pipeline + resolver.
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
        return "objective-entry"

    async def on_settings_changed(self, namespace: str, key: str) -> None:
        """Re-wire the objective entry adapter from the live resolver."""
        if (namespace, key) not in _WATCHED:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected pair",
            )
            return
        from synthorg.engine.pipeline.entry.boot import (  # noqa: PLC0415
            wire_real_objective_entry,
        )

        try:
            await wire_real_objective_entry(self._app_state, hot_swap=True)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="objective_entry_adapter",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
