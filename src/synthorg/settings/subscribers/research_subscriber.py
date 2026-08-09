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

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import describe_changes

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
        # The research WEB source bakes in the native web-search provider (its
        # preset, bound connection, and result ceiling) at construction, so a
        # change to any of these keys must rebuild research too -- otherwise the
        # runtime's tool registry (rebuilt by RuntimeReloadSettingsSubscriber)
        # would pick up the new provider while research kept the stale one.
        ("tools", "web_search_enabled"),
        ("tools", "web_search_provider"),
        ("tools", "web_search_connection"),
        ("tools", "web_search_max_results"),
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

    A rebuild that DECLINES is a different outcome from one that fails. The
    write cleared the bound pair or pointed it at a connection that is not
    registered, which is a legitimate thing for an operator to do, so the
    research slice is cleared and the write succeeds; the reconciler reports
    the named condition on ``GET /subsystems``. Leaving the previous service
    in place there would keep answering research requests through the
    provider and model that were just removed.

    Every other error during rebuild propagates to the dispatcher, which logs
    it with full subscriber context and continues; the previously wired
    service stays in place, because nothing has said it should not.

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
        changes: Sequence[tuple[str, str]],
    ) -> None:
        """Rebuild the research service from current settings and swap it in.

        One rebuild per batch: the service is rebuilt from every watched key,
        so repeating it per key would rebuild the same service several times.

        Args:
            changes: The watched writes this rebuild carries.
        """
        from synthorg.api.lifecycle_helpers.feature_wiring import (  # noqa: PLC0415
            _build_and_wire_research,
        )
        from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415
        from synthorg.research.state import ResearchStateSlice  # noqa: PLC0415

        trigger = describe_changes(changes)
        registry = self._app_state.slice(ProvidersStateSlice).registry
        if registry is None:
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                trigger=trigger,
                note="no provider registry wired; rebuild deferred to boot wiring",
            )
            return
        try:
            await _build_and_wire_research(
                self._app_state,
                provider_registry=registry,
                runtime_settings=self._settings_service,
            )
        except SubsystemDeclinedError as exc:
            # A decline is an answer, not a failure: clearing the bound pair
            # is a legitimate write, and raising would fail the operator's
            # settings change for doing exactly what they asked. The
            # reconciler reports the named condition on GET /subsystems.
            #
            # The slice is cleared, not merely left alone: the builder swaps
            # only on success, so keeping the previous service would go on
            # answering research requests through the provider and model the
            # operator has just removed.
            self._app_state.swap_slice(ResearchStateSlice())
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                trigger=trigger,
                note=safe_error_description(exc),
            )
            return
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="research_service",
                trigger=trigger,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            SETTINGS_SUBSCRIBER_NOTIFIED,
            subscriber=self.subscriber_name,
            trigger=trigger,
            note="research service rebuilt and swapped",
        )
