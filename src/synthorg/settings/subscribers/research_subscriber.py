"""Research settings subscriber: rebuilds the research service on change.

The research service bakes its provider, model, strategy discriminators, and
tuning thresholds into the strategy objects at construction. A change to any of
those keys only goes live through a rebuild, so this subscriber re-runs the
research wiring factory and atomically swaps the new service onto the research
state slice. In-flight research runs hold the previously-captured service, so a
swap never disrupts a running brief.

The strategies also bake in the provider registry's drivers (and their retry
handlers), so a ``providers.retry_max_attempts`` change is watched too: that key
makes ``ProviderSettingsSubscriber`` rebuild the registry, after which the wired
service would otherwise keep using the old retry handlers until some research
key changed. ``ProviderSettingsSubscriber`` is registered ahead of this one, so
by the time this subscriber fires the swapped-in registry is live and the
rebuild picks it up.

The master switch ``research.enabled`` is NOT watched here: it is enforced live
per request at the research MCP handlers, so it needs no rebuild.
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
        ("research", "model"),
        ("research", "query_planner"),
        ("research", "credibility_triage"),
        ("research", "deduplicator"),
        ("research", "synthesizer"),
        ("research", "triage_batch_size"),
        ("research", "hybrid_prefilter_factor"),
        ("research", "dedup_similarity_threshold"),
        ("research", "per_query_limit"),
        # A registry rebuild (driven by ProviderSettingsSubscriber, registered
        # ahead of this one) re-bakes the strategies' retry handlers.
        ("providers", "retry_max_attempts"),
    }
)


class ResearchSettingsSubscriber:
    """Rebuild + swap the research service on a research-namespace change.

    On any watched key change, resolves the live provider registry and re-runs
    the research wiring factory, which rebuilds the ``ResearchConfig`` from the
    current settings and swaps a fresh service onto ``ResearchStateSlice``. A
    rebuild is skipped (advisory log) when no provider registry is wired yet, so
    a settings change before the registry comes online does not crash; the
    boot-time wiring then builds the service with the up-to-date values.

    Errors during rebuild propagate to the dispatcher, which logs them with full
    subscriber context and continues; the previously wired service stays in
    place.

    Args:
        app_state: Application state holding the slices + service swap surface.
        settings_service: Settings service the wiring factory reads from.
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
        """Return the research-namespace keys this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logging."""
        return "research-settings"

    async def on_settings_changed(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """Rebuild the research service from current settings and swap it in.

        Args:
            namespace: Changed setting namespace.
            key: Changed setting key.
        """
        from synthorg.api.lifecycle_helpers.feature_wiring import (  # noqa: PLC0415
            _build_and_wire_research,
        )
        from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415

        registry = self._app_state.slice(ProvidersStateSlice).registry
        if registry is None:
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="no provider registry wired; rebuild deferred to boot wiring",
            )
            return
        try:
            await _build_and_wire_research(
                self._app_state,
                provider_registry=registry,
                runtime_settings=self._settings_service,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="research_service",
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
            note="research service rebuilt and swapped",
        )
