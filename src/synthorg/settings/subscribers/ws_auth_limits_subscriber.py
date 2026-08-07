"""WebSocket / auth-revalidation limits settings subscriber.

Pushes operator edits to the WS auth-handshake / per-frame timeouts and the
auth-revalidation sliding-window limits onto the live ``WsAuthLimits`` object
(``app_state.ws_auth_limits``), which the ``/ws`` handler and SSE stream sample
per connection-open. The startup application of the same settings lives in
``api.lifecycle_helpers.config_apply._apply_ws_auth_timeout`` /
``_apply_ws_dos_settings``; this subscriber is the hot-reload counterpart.
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.service import SettingsService
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

_NAMESPACE = SettingNamespace.API.value
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    (_NAMESPACE, k)
    for k in (
        "ws_auth_timeout_seconds",
        "ws_frame_timeout_seconds",
        "auth_revalidate_window_seconds",
        "auth_revalidate_max_failures",
    )
)


class WsAuthLimitsSettingsSubscriber:
    """Apply WS / auth-revalidation knob changes onto ``WsAuthLimits``.

    Resolves the changed key via the live resolver and calls the matching
    ``WsAuthLimits.set_*`` method. The setters validate type + range and
    raise (with a structured warning) on a bad value, leaving the prior
    value in place; the subscriber re-raises so the dispatcher records the
    failure with subscriber context.

    Args:
        app_state: Application state owning ``ws_auth_limits`` + the resolver.
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
        return "ws-auth-limits"

    async def _apply(self, namespace: str, key: str) -> None:
        """Resolve *key* with the right resolver method and call its setter.

        ``ws_auth_timeout_seconds`` is a float; the other three are ints, and
        their setters reject non-ints, so the resolver method is chosen per
        key (direct method references so a setter rename fails type-checking).
        """
        resolver = config_resolver_of(self._app_state)
        limits = self._app_state.ws_auth_limits
        if key == "ws_auth_timeout_seconds":
            limits.set_auth_timeout_seconds(await resolver.get_float(namespace, key))
            return
        value = await resolver.get_int(namespace, key)
        if key == "ws_frame_timeout_seconds":
            limits.set_frame_timeout_seconds(value)
        elif key == "auth_revalidate_window_seconds":
            limits.set_auth_revalidate_window_seconds(value)
        else:
            limits.set_auth_revalidate_max_failures(value)

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Apply each changed value to ``WsAuthLimits``.

        Each pair drives its own setter, so one that fails says nothing
        about the rest: the loop runs to the end and a non-critical
        failure is raised after it. Stopping at the first would leave the
        later limits persisted and not live until an unrelated write, and
        the dispatcher records one failure per subscriber call either way.
        A critical error still aborts on the spot.

        Args:
            changes: The watched writes to apply.

        Raises:
            Exception: The first non-critical per-pair failure, re-raised
                once every pair has been attempted.
        """
        deferred: Exception | None = None
        for namespace, key in changes:
            try:
                await self._apply_change(namespace, key)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                deferred = deferred or exc
        if deferred is not None:
            raise deferred

    async def _apply_change(self, namespace: str, key: str) -> None:
        """Resolve the new value and apply it to ``WsAuthLimits``."""
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
            await self._apply(namespace, key)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="ws_auth_limits",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
