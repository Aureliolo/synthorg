"""Provider settings subscriber -- rebuilds ModelRouter on strategy change."""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager

from synthorg.api.state import AppState
from synthorg.config.provider_schema import ProviderConfig
from synthorg.config.schema import RootConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.routing.router import ModelRouter
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import describe_changes

logger = get_logger(__name__)

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        ("providers", "routing_strategy"),
        ("providers", "retry_max_attempts"),
    }
)


@contextmanager
def _swap_failure_logged(
    service: str, context: Mapping[str, object] | None = None
) -> Iterator[None]:
    """Log a failed hot-swap with context, then let it reach the dispatcher.

    Both rebuilds here promise the same thing: on failure the service
    already in ``AppState`` stays, and the dispatcher hears about it. The
    swallow-and-continue variant would leave an operator's setting silently
    unapplied, so the error is re-raised after it has been made readable.

    Args:
        service: Which service could not be swapped, for the log line.
        context: Extra fields resolved as the body runs (the strategy a
            router rebuild had got as far as reading), read at failure time
            rather than passed up front so a fault before that read still
            logs what was known.

    Yields:
        Nothing; the caller's body runs inside the guard.
    """
    try:
        yield
    except Exception as exc:
        reraise_critical(exc)
        logger.error(
            SETTINGS_SERVICE_SWAP_FAILED,
            service=service,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            **(dict(context) if context is not None else {}),
        )
        raise


class ProviderSettingsSubscriber:
    """React to provider-namespace settings changes.

    On ``routing_strategy`` change, rebuilds :class:`ModelRouter` with the
    new strategy and swaps it into ``AppState``. NOTE: the wired
    ``ProvidersStateSlice.model_router`` is consumed only by the Prometheus
    ``model_router`` label fetcher; agent model selection runs through the
    separate stakes / work-routing policies, so this change updates the
    exported metric label, not live routing behaviour.

    On ``retry_max_attempts`` change, rebuilds the :class:`ProviderRegistry`
    so the new org-wide retry cap applies live without a restart, then
    triggers a runtime-services rebuild so the running ``AgentEngine`` (which
    captured the registry at construction) adopts the rebuilt one. The cap is
    baked into each driver's :class:`RetryHandler` at build time, so a change
    only takes effect through a registry rebuild; rebuilding + reloading here
    is the seam that makes the setting live for the completion path. The
    rebuild resolves the current provider set (DB-persisted blob, else the
    boot template) and re-binds the always-on credential catalogue so
    ``connection_name`` auth keeps resolving. A rebuild is skipped while a
    cassette session is active (the recorded-LLM seam is baked in at process
    start), in which case the new cap applies on the next restart.

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
        changes: Sequence[tuple[str, str]],
    ) -> None:
        """Handle a batch of provider setting changes.

        ``routing_strategy`` triggers a :class:`ModelRouter` rebuild;
        ``retry_max_attempts`` triggers a :class:`ProviderRegistry` rebuild so
        the new retry cap goes live. Each rebuild runs at most once for the
        batch, since each re-reads its own setting. Other keys are advisory
        and logged at INFO level.

        The two rebuilds read different settings and swap different
        services, so a failure in one says nothing about the other: the
        second is attempted regardless and a non-critical failure is held
        until both have run. Raising at the first would leave the second
        setting persisted and not live until an unrelated write or a
        restart, since the dispatcher sees one exception per subscriber
        call either way. A critical error still aborts on the spot.

        Args:
            changes: The watched writes this rebuild carries.

        Raises:
            Exception: The first non-critical rebuild failure, re-raised
                once every independent rebuild in the batch has run, so
                the dispatcher records the batch as failed.
        """
        pairs = set(changes)
        rebuilt = False
        deferred: Exception | None = None
        if ("providers", "routing_strategy") in pairs:
            rebuilt = True
            try:
                await self._rebuild_router()
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                deferred = exc
        if ("providers", "retry_max_attempts") in pairs:
            rebuilt = True
            try:
                await self._rebuild_registry("retry_max_attempts")
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                deferred = deferred or exc
        if deferred is not None:
            raise deferred
        if not rebuilt:
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                trigger=describe_changes(changes),
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
        attempted: dict[str, object] = {"attempted_strategy": None}
        with _swap_failure_logged("model_router", attempted):
            result = await self._settings_service.get(
                "providers",
                "routing_strategy",
            )
            attempted["attempted_strategy"] = result.value
            config = self._app_state.config
            new_routing = config.routing.model_copy(
                update={"strategy": result.value},
            )
            new_router = ModelRouter(
                new_routing,
                dict(config.providers),
            )
            from synthorg.providers.state import (  # noqa: PLC0415
                ProvidersStateSlice,
            )

            self._app_state.wire(ProvidersStateSlice, model_router=new_router)

    def _cassette_holds(
        self, registry: ProviderRegistry | None, key: str, *, note: str
    ) -> bool:
        """Whether *registry* is cassette-bound, so a swap must stand down.

        Asked twice per rebuild, before and after the resolving awaits: a
        concurrent setup-complete reinit can install a cassette-bound
        registry mid-flight, and swapping over it would route recorded-LLM
        traffic to the live provider.

        Args:
            registry: The registry read from state, or ``None`` when none is
                wired yet.
            key: The setting key that triggered the rebuild, for telemetry.
            note: Which of the two checks declined, so the log says whether
                the cassette was already there or arrived mid-rebuild.

        Returns:
            ``True`` when a cassette session is active on *registry*.
        """
        if registry is None or registry.cassette_session is None:
            return False
        logger.info(
            SETTINGS_SUBSCRIBER_NOTIFIED,
            subscriber=self.subscriber_name,
            namespace="providers",
            key=key,
            note=note,
        )
        return True

    async def _rebuild_registry(self, key: str) -> None:
        """Rebuild the ProviderRegistry from live settings and swap it in.

        Triggered by a ``retry_max_attempts`` change; *key* names it and is
        echoed in the telemetry. Resolves the live retry cap and the current
        provider set (the DB-persisted blob, falling back to the boot
        template), rebuilds the registry with the catalogue re-bound, and
        hot-swaps it. Skipped
        while a cassette session is active, since the recorded-LLM seam is
        baked in at process start and the change then applies on the next
        restart. On failure the existing registry stays in place; the error is
        logged with context before re-raising to the dispatcher.
        """
        from synthorg.integrations.state import (  # noqa: PLC0415
            provider_credential_catalog_of,
        )
        from synthorg.providers.management._persistence import (  # noqa: PLC0415
            resolve_retry_max_attempts,
        )
        from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415
        from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

        with _swap_failure_logged("provider_registry"):
            current = self._app_state.slice(ProvidersStateSlice).registry
            if self._cassette_holds(
                current,
                key,
                note="cassette active -- change applies on next restart",
            ):
                return
            resolver = config_resolver_of(self._app_state)
            retry_max_attempts = await resolve_retry_max_attempts(resolver)
            provider_configs = dict(await resolver.get_provider_configs())
            new_registry = ProviderRegistry.from_config(
                provider_configs,
                connection_catalog=provider_credential_catalog_of(self._app_state),
                retry_max_attempts=retry_max_attempts,
            )
            # Re-read after the awaits; see ``_cassette_holds`` for why.
            live = self._app_state.slice(ProvidersStateSlice).registry
            if self._cassette_holds(
                live,
                key,
                note="cassette became active during rebuild -- skipped swap",
            ):
                return
            await self._apply_registry_swap(
                new_registry,
                live,
                provider_configs,
                trigger=f"setting:providers.{key}",
            )
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace="providers",
                key=key,
                note="provider registry rebuilt, swapped, and runtime reloaded",
            )

    async def _apply_registry_swap(
        self,
        new_registry: ProviderRegistry,
        previous_registry: ProviderRegistry | None,
        provider_configs: Mapping[str, ProviderConfig],
        *,
        trigger: str,
    ) -> None:
        """Swap in *new_registry* and reload the runtime, rolling back on failure.

        *trigger* names the setting whose write prompted this, so a reload in
        the log can be traced to the write that caused it; several watched keys
        rebuild the registry, and a shared constant would make them
        indistinguishable exactly when one of them is misbehaving.

        The slice swap commits before the runtime reload (the running
        ``AgentEngine`` captured the registry at construction, so a slice swap
        alone leaves the completion path on the old retry cap). If the reload
        raises, the slice and the engine would diverge, so the pre-swap registry
        -- which may have been unset (``None``), expressible only via ``wire``
        and not the non-None ``swap_provider_registry`` shim -- is restored,
        its health and billing bindings re-applied, and the runtime re-healed
        before the original error propagates. The runtime
        builder is imported before the swap so an import failure cannot leave a
        committed swap un-reloaded. ``MemoryError`` / ``RecursionError`` skip the
        rollback so a fatal condition is not driven through a second reload.
        """
        from synthorg.providers._driver_binding import (  # noqa: PLC0415
            rebind_provider_set,
        )
        from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415
        from synthorg.workers.runtime_builder import (  # noqa: PLC0415
            reload_runtime_services,
        )

        # The rebuilt registry's drivers are new and report nowhere, and the
        # ledger still stamps how the replaced set charged, until both are
        # pointed at this one; bind before the swap commits.
        rebind_provider_set(
            self._app_state,
            new_registry,
            provider_configs,
            clock=self._app_state.clock,
        )
        self._app_state.swap_provider_registry(new_registry)
        try:
            await reload_runtime_services(self._app_state, trigger=trigger)
        except Exception as reload_exc:
            reraise_critical(reload_exc)
            self._app_state.wire(ProvidersStateSlice, registry=previous_registry)
            # Restored before the rollback reload, and for the same reason the
            # bind above precedes the swap: the drivers that go back into
            # service are the previous registry's, and health and billing
            # would otherwise still be pointed at the replacement that failed.
            # Skipped when there was no previous registry, since there is then
            # nothing to attach recorders to.
            if previous_registry is not None:
                rebind_provider_set(
                    self._app_state,
                    previous_registry,
                    provider_configs,
                    clock=self._app_state.clock,
                )
            try:
                await reload_runtime_services(
                    self._app_state, trigger=f"{trigger}-rollback"
                )
            except Exception as heal_exc:  # noqa: BLE001
                reraise_critical(heal_exc)
                logger.error(
                    SETTINGS_SERVICE_SWAP_FAILED,
                    service="provider_registry",
                    error_type=type(heal_exc).__name__,
                    error=safe_error_description(heal_exc),
                    note="rollback runtime reload failed -- registry "
                    "restored but runtime may be inconsistent",
                )
            raise
