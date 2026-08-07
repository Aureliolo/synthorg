"""Meta self-improvement settings subscriber.

Invalidates the cached :class:`~synthorg.meta.config.SelfImprovementConfig`
on the meta slice when an operator edits the structural ``meta.self_improvement``
blob OR any setting the feature overlay reads. The meta slice caches the parsed
config so the read endpoints do not re-parse per request; this subscriber wires
the cache field back to ``None`` so the next read reloads the fresh value.

The cache is not only a read-path optimisation: subsystem activations build
from it (``_si_config`` in the registry hands it to every wirer that needs a
per-feature model), so a key the overlay reads but this subscriber does not
watch produces a subsystem that rebuilds on a settings write and then
reconstructs itself from the pre-write value. That is why the watch set is
derived from :func:`overlaid_setting_keys` rather than listed here: the two
cannot drift apart.
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.meta._config_overlay import overlaid_setting_keys
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.service_protocol import SettingsServiceProtocol
from synthorg.settings.subscriber import describe_changes

logger = get_logger(__name__)

# The structural blob plus every setting the overlay reads. Derived rather
# than listed: a hand-written list is only correct on the day it is written,
# and the cost of a missing key is silent, because the write reaches the
# store, triggers a rebuild, and the rebuild reads the stale cache.
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {("meta", "self_improvement")} | overlaid_setting_keys()
)


class MetaSelfImprovementSettingsSubscriber:
    """Invalidate the cached ``SelfImprovementConfig`` on a config edit.

    Holds :class:`AppState` (where the cache lives) and the settings service
    (for parity with peer subscribers). On a watched-key change it wires
    ``MetaStateSlice.self_improvement_config`` back to ``None`` so the next
    :func:`self_improvement_config_of` read reloads the operator's new value.

    Args:
        app_state: Application state that owns the cached config.
        settings_service: Settings service held for symmetry with peers.
            Typed as the protocol because nothing here reaches past it.
    """

    def __init__(
        self,
        app_state: AppState,
        settings_service: SettingsServiceProtocol,
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
        return "meta-self-improvement"

    async def on_settings_changed(
        self,
        changes: Sequence[tuple[str, str]],
    ) -> None:
        """Invalidate the cached config so the next read reloads it.

        One invalidation per batch: the cache is a single field, so clearing
        it once covers every key in the batch.

        Args:
            changes: The watched writes that prompted the invalidation.
        """
        from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

        try:
            self._app_state.wire(MetaStateSlice, self_improvement_config=None)
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                trigger=describe_changes(changes),
                note="invalidated cached self-improvement config",
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="meta_self_improvement",
                trigger=describe_changes(changes),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
