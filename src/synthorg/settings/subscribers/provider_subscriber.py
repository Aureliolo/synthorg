"""Provider settings subscriber -- rebuilds ModelRouter on strategy change."""

from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.providers.routing.router import ModelRouter
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

    On ``retry_max_attempts`` change, rebuilds the :class:`ProviderRegistry`
    so the new org-wide retry cap applies live without a restart. The cap
    is baked into each driver's :class:`RetryHandler` at build time, so a
    change only takes effect through a registry rebuild; rebuilding here
    is the seam that makes the setting live. The rebuild resolves the
    current provider set (DB-persisted blob, else the boot template) and
    re-binds the always-on credential catalog so ``connection_name`` auth
    keeps resolving. A rebuild is skipped while a cassette session is
    active (the recorded-LLM seam is baked in at process start), in which
    case the new cap applies on the next restart.

    Errors during rebuild propagate to the dispatcher, which logs
    them with full subscriber context and continues to the next
    subscriber.  The previously wired service stays in place.

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

        ``routing_strategy`` triggers a :class:`ModelRouter` rebuild;
        ``retry_max_attempts`` triggers a :class:`ProviderRegistry` rebuild
        so the new retry cap goes live. Other keys are advisory and logged
        at INFO level.

        Args:
            namespace: Changed setting namespace.
            key: Changed setting key.
        """
        if namespace == "providers" and key == "routing_strategy":
            await self._rebuild_router()
        elif namespace == "providers" and key == "retry_max_attempts":
            await self._rebuild_registry()
        else:
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="advisory -- no service rebuild required",
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
            from synthorg.providers.state import (  # noqa: PLC0415
                ProvidersStateSlice,
            )

            self._app_state.wire(ProvidersStateSlice, model_router=new_router)
        except Exception as exc:
            reraise_critical(exc)
            logger.error(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="model_router",
                attempted_strategy=attempted_strategy,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    async def _rebuild_registry(self) -> None:
        """Rebuild the ProviderRegistry with the new retry cap and swap it in.

        Resolves the live ``providers.retry_max_attempts`` value and the
        current provider set (the DB-persisted blob, falling back to the
        boot template), rebuilds the registry with the catalog re-bound, and
        hot-swaps it. Skipped while a cassette session is active, since the
        recorded-LLM seam is baked in at process start and the cap then
        applies on the next restart. On failure the existing registry stays
        in place; the error is logged with context before re-raising to the
        dispatcher.
        """
        from synthorg.integrations.state import (  # noqa: PLC0415
            provider_credential_catalog_of,
        )
        from synthorg.providers.management._persistence import (  # noqa: PLC0415
            resolve_retry_max_attempts,
        )
        from synthorg.providers.registry import ProviderRegistry  # noqa: PLC0415
        from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415
        from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

        try:
            current = self._app_state.slice(ProvidersStateSlice).registry
            if current is not None and current.cassette_session is not None:
                logger.info(
                    SETTINGS_SUBSCRIBER_NOTIFIED,
                    subscriber=self.subscriber_name,
                    namespace="providers",
                    key="retry_max_attempts",
                    note="cassette active -- retry cap applies on next restart",
                )
                return
            resolver = config_resolver_of(self._app_state)
            retry_max_attempts = await resolve_retry_max_attempts(resolver)
            provider_configs = dict(await resolver.get_provider_configs())
            new_registry = ProviderRegistry.from_config(
                provider_configs,
                connection_catalog=provider_credential_catalog_of(self._app_state),
                retry_max_attempts=retry_max_attempts,
            )
            self._app_state.swap_provider_registry(new_registry)
        except Exception as exc:
            reraise_critical(exc)
            logger.error(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="provider_registry",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
