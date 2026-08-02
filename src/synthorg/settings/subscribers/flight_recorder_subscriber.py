"""Cockpit flight-recorder settings subscriber.

Re-resolves the three cockpit recorder keys onto the live
:class:`~synthorg.engine.flight_recording.sink.LiveFlightRecorderSink` the boot
engine holds. The sink picks its delegate per batch, so enabling recording or
switching strategy applies to the next agent run with nothing rebuilt.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.state import EngineStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_NAMESPACE = SettingNamespace.COCKPIT.value
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        (_NAMESPACE, "flight_recorder_enabled"),
        (_NAMESPACE, "flight_recorder_sink_strategy"),
        (_NAMESPACE, "flight_recorder_summary_max_chars"),
    }
)


class FlightRecorderSettingsSubscriber:
    """Push cockpit recorder-config changes onto the live sink.

    Args:
        app_state: Application state owning the sink and the resolver.
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
        return "flight-recorder"

    async def on_settings_changed(self, namespace: str, key: str) -> None:
        """Re-resolve the recorder configuration onto the live sink.

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
        sink = self._app_state.slice(EngineStateSlice).flight_recorder_sink
        if sink is None:
            return
        # Local: ``synthorg.workers`` pulls the whole agent-engine surface
        # (tool factories, sandbox backends, execution loops) behind it, and
        # this subscriber is imported wherever settings are, including the CLI
        # paths that never build an engine.
        from synthorg.workers._agent_engine_collaborators import (  # noqa: PLC0415
            refresh_flight_recorder_sink,
        )

        try:
            await refresh_flight_recorder_sink(self._app_state, sink)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="flight_recorder_sink",
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
            note="flight-recorder configuration applied to the live sink",
        )
