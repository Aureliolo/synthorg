# module-kind: code
"""The provider half of construction-time auto-wiring.

Building the registry needs two boot-time settings resolved before the settings
layer exists, and the health tracker needs binding to the connections surface so
both screens report one verdict. Kept beside ``auto_wire_phase1`` rather than
inside it: that module is the phase's assembly order, and these are the provider
subsystem's own construction details, which is a different thing to read.
"""

from synthorg.config.schema import RootConfig
from synthorg.core.clock import Clock
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.providers.cassette import CassetteConfig
from synthorg.providers.health_tracker import ProviderHealthTracker
from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)


def bind_connection_health_to_tracker(
    tracker: ProviderHealthTracker,
    *,
    clock: Clock,
) -> None:
    """Route provider-connection health through the provider tracker.

    The Connections screen must report the same provider verdict as the
    Providers screen, so the LLM-provider connection checker resolves
    health from this tracker; connections outside the ``provider-<name>``
    convention resolve to ``None`` and keep the reachability probe.

    Args:
        tracker: Where the verdict is read from.
        clock: The same clock the outcomes were recorded on. The tracker
            measures its window back from the reference time it is given, so
            reading on wall time while recording on an injected clock can put
            every record outside the window and answer with a verdict about
            nothing.
    """
    from synthorg.integrations.health.prober import (  # noqa: PLC0415
        bind_provider_health_lookup,
    )
    from synthorg.providers.health import ProviderHealthSummary  # noqa: PLC0415
    from synthorg.providers.management._credential_helpers import (  # noqa: PLC0415
        provider_name_for_connection,
    )

    async def _lookup(connection_name: str) -> ProviderHealthSummary | None:
        provider_name = provider_name_for_connection(connection_name)
        if provider_name is None:
            return None
        return await tracker.get_summary(provider_name, now=clock.now())

    bind_provider_health_lookup(_lookup)


def resolve_cassette_config() -> CassetteConfig | None:
    """Resolve the boot-time cassette config (Cat-2: env > default).

    Uses the sanctioned pre-init bootstrap resolver -- no ``os.environ`` read
    in provider code.

    Returns:
        The resolved cassette config, or ``None`` when the seam is inert so the
        registry holds the concrete drivers unchanged.
    """
    from pathlib import Path  # noqa: PLC0415

    from synthorg.providers.cassette import (  # noqa: PLC0415
        CassetteConfig,
        CassetteMode,
    )
    from synthorg.settings.bootstrap_resolver import (  # noqa: PLC0415
        resolve_init_value,
    )
    from synthorg.settings.enums import SettingNamespace  # noqa: PLC0415

    mode_raw = str(
        resolve_init_value(SettingNamespace.PROVIDERS, "cassette_mode").value
    ).strip()
    mode = CassetteMode(mode_raw)
    if mode is CassetteMode.OFF:
        return None
    path_resolved = resolve_init_value(
        SettingNamespace.PROVIDERS, "cassette_path"
    ).value
    path = Path(str(path_resolved)) if path_resolved else None
    return CassetteConfig(mode=mode, path=path)


def resolve_retry_max_attempts() -> int | None:
    """Resolve the boot-time provider retry cap (Cat-2: env > default).

    Reads ``providers.retry_max_attempts`` through the sanctioned pre-init
    bootstrap resolver (env > registered default), so a fresh boot honours
    ``SYNTHORG_PROVIDERS_RETRY_MAX_ATTEMPTS`` without an ``os.environ`` read
    in provider code. The DB-stored value is applied later, when the
    settings layer is connected, by ``ProviderSettingsSubscriber`` on change
    and by the provider hot-reload / setup-reinit paths.

    Returns:
        The resolved retry cap, or ``None`` when the registered default is
        absent so the registry leaves each provider's own retry untouched.
    """
    from synthorg.settings.bootstrap_resolver import (  # noqa: PLC0415
        resolve_init_value,
    )
    from synthorg.settings.enums import SettingNamespace  # noqa: PLC0415

    def _parse_int(raw: str) -> int | None:
        try:
            return int(raw)
        except ValueError:
            logger.warning(
                API_APP_STARTUP,
                action="retry_max_attempts_parse_failed",
                key="providers.retry_max_attempts",
                note=(
                    "value is not a valid integer; retry cap not applied, "
                    "each provider keeps its own retry config"
                ),
            )
            return None

    resolved = resolve_init_value(
        SettingNamespace.PROVIDERS,
        "retry_max_attempts",
        parse=_parse_int,
    ).value
    return resolved if isinstance(resolved, int) else None


def wire_provider_registry(
    effective_config: RootConfig,
) -> ProviderRegistry:
    """Create a ProviderRegistry from config.

    Returns:
        The configured provider registry.
    """
    try:
        registry = ProviderRegistry.from_config(
            effective_config.providers,
            cassette=resolve_cassette_config(),
            retry_max_attempts=resolve_retry_max_attempts(),
        )
    except Exception as exc:
        log_exception_redacted(
            logger,
            API_APP_STARTUP,
            exc,
            note="Failed to build provider registry from config",
        )
        raise
    logger.info(API_SERVICE_AUTO_WIRED, service="provider_registry")
    return registry
