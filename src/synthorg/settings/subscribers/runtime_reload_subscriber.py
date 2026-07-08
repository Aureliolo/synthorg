"""Runtime-services reload settings subscriber.

Triggers ``workers.runtime_builder.reload_runtime_services`` when an operator
edits a setting that ``build_runtime_services`` already re-reads from the live
resolver but only consults on a rebuild: the engine quality-classifier /
model-matcher knobs, the external-API runtime gate, and the coordination
middleware toggle. The rebuild hot-swaps the agent engine, coordinator, work
pipeline, and entry adapters with no process restart.

A single subscriber coalesces these keys because they all converge on the same
``reload_runtime_services`` call (serialised by its module lock); one reload per
change is correct and cheap relative to a restart.
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
    {
        ("engine", "classifier_rule_matched_confidence"),
        ("engine", "classifier_fallback_confidence"),
        ("engine", "classification_detector_timeout_seconds"),
        ("engine", "matcher_min_usable_parameters"),
        ("external_api", "enabled"),
        ("external_api", "provider_type"),
        ("coordination", "enable_coordination_middleware"),
        # The coordinator builds eagerly and hard-requires a non-blank
        # decomposition model, so setting it must rebuild the coordinator
        # live: without this, first-run setup writes the model AFTER a
        # capability toggle already tried (and failed) to build the
        # coordinator, leaving it broken until a manual restart.
        ("coordination", "decomposition_model"),
        ("design", "image_generation_enabled"),
        ("design", "image_model"),
    }
)


class RuntimeReloadSettingsSubscriber:
    """Rebuild runtime services on a watched engine/external_api/coordination edit.

    Args:
        app_state: Application state passed to ``reload_runtime_services``.
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
        return "runtime-reload"

    async def on_settings_changed(self, namespace: str, key: str) -> None:
        """Trigger a runtime-services rebuild so the new value goes live."""
        if (namespace, key) not in _WATCHED:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected pair",
            )
            return
        from synthorg.workers.runtime_builder import (  # noqa: PLC0415
            reload_runtime_services,
        )

        try:
            await reload_runtime_services(self._app_state)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="runtime_services",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
