"""Provider settings subscriber -- rebuilds ModelRouter on strategy change."""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.providers.routing.router import ModelRouter

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.config.schema import RootConfig
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        ("providers", "routing_strategy"),
        ("providers", "retry_max_attempts"),
    }
)


class ProviderSettingsSubscriber:
    """React to provider-namespace settings changes.

    On ``routing_strategy`` change, rebuilds :class:`ModelRouter`
    with the new strategy and swaps it into ``AppState``.

    ``retry_max_attempts`` is advisory-only: it is read through
    :class:`ConfigResolver` at use time and does not require a
    service rebuild.  It is watched so the operator sees a log entry
    confirming the change was detected.

    Errors during rebuild propagate to the dispatcher, which logs
    them with full subscriber context and continues to the next
    subscriber.  The old ``ModelRouter`` remains in ``AppState``.

    Args:
        config: Root company configuration (providers + routing).
        app_state: Application state for service swap.
        settings_service: Settings service for reading new values.
    """

    def __init__(
        self,
        config: RootConfig,
        app_state: AppState,
        settings_service: SettingsService,
    ) -> None:
        self._config = config
        self._app_state = app_state
        self._settings_service = settings_service

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return provider-namespace keys this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name."""
        return "provider-settings"

    async def on_settings_changed(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """Handle a provider setting change.

        Only ``routing_strategy`` triggers a :class:`ModelRouter`
        rebuild.  Other keys are advisory and logged at INFO level.

        Args:
            namespace: Changed setting namespace.
            key: Changed setting key.
        """
        if namespace == "providers" and key == "routing_strategy":
            await self._rebuild_router()
        else:
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="advisory -- read through ConfigResolver at use time",
            )

    async def _rebuild_router(self) -> None:
        """Build a new ModelRouter from current settings and swap it in.

        Reads the current ``routing_strategy`` value from
        :class:`SettingsService`, extracts the string value from the
        returned :class:`SettingValue`, and constructs a new router.
        On failure, the existing ``ModelRouter`` in ``AppState``
        remains unchanged.  Errors are logged with actionable context
        via ``SETTINGS_SERVICE_SWAP_FAILED`` before re-raising to the
        dispatcher.
        """
        attempted_strategy: str | None = None
        try:
            result = await self._settings_service.get(
                "providers",
                "routing_strategy",
            )
            attempted_strategy = result.value
            config = self._app_state.config
            new_routing = config.routing.model_copy(
                update={"strategy": attempted_strategy},
            )
            new_router = ModelRouter(
                new_routing,
                dict(config.providers),
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.error(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="model_router",
                attempted_strategy=attempted_strategy,
            )
            raise
        self._app_state.swap_model_router(new_router)
