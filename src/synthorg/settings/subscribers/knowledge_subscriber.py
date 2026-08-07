"""Knowledge settings subscriber: rebuilds the synthesis arm on change.

The knowledge synthesiser bakes its model, provider, strategy, and max-chunks
into the ``KnowledgeService`` at construction. A change to any of those keys
only goes live through a rebuild, so this subscriber re-runs the knowledge
wiring factory (which rebuilds the synthesiser and the service around it) and
atomically swaps the new service onto the knowledge state slice. In-flight
``ask`` / ``search`` calls hold the previously-captured service, so a swap never
disrupts a running query.

The synthesiser also bakes in the provider registry's drivers (and their retry
handlers), so a ``providers.retry_max_attempts`` change is watched too: that key
makes ``ProviderSettingsSubscriber`` rebuild the registry, after which the wired
synthesiser would otherwise keep using the old retry handlers until some
knowledge key changed. ``ProviderSettingsSubscriber`` is registered ahead of
this one, so by the time this subscriber fires the swapped-in registry is live
and the rebuild picks it up.

The master switch ``knowledge.enabled`` and the ``/ask`` gate
``knowledge.synthesis_enabled`` are NOT watched here: both are enforced live per
request at the knowledge MCP handlers, so they need no rebuild.
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.subscriber import describe_changes

logger = get_logger(__name__)

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        ("knowledge", "synthesis_model"),
        ("knowledge", "synthesis_synthesizer"),
        ("knowledge", "synthesis_max_chunks"),
        # A registry rebuild (driven by ProviderSettingsSubscriber, registered
        # ahead of this one) re-bakes the synthesiser's retry handlers.
        ("providers", "retry_max_attempts"),
    }
)


class KnowledgeSettingsSubscriber:
    """Rebuild + swap the knowledge service on a synthesis-config change.

    On any watched key change, resolves the live provider registry and re-runs
    the knowledge wiring factory, which rebuilds the synthesiser and the
    ``KnowledgeService`` around it and swaps the fresh service onto
    ``KnowledgeStateSlice``. The factory reads the synthesis settings off
    ``app_state`` itself, so no settings service is held here.

    A synthesis-build failure during the rebuild is surfaced under
    ``SETTINGS_SERVICE_SWAP_FAILED`` (not the startup event) so an operator who
    sets an invalid synthesis model/provider sees the breakage. Other rebuild
    errors propagate to the dispatcher, which logs them and continues; the
    previously wired service stays in place.

    Args:
        app_state: Application state holding the slices + service swap surface.
    """

    def __init__(self, app_state: AppState) -> None:
        self._app_state = app_state

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return the knowledge-namespace keys this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logging."""
        return "knowledge-settings"

    async def on_settings_changed(
        self,
        changes: Sequence[tuple[str, str]],
    ) -> None:
        """Rebuild the knowledge service from current settings and swap it in.

        One rebuild per batch: the service is rebuilt from every watched key,
        so repeating it per key would rebuild the same service several times.

        Args:
            changes: The watched writes this rebuild carries.
        """
        from synthorg.api.lifecycle_helpers.knowledge_wiring import (  # noqa: PLC0415
            _build_and_wire_knowledge,
        )
        from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415

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
            await _build_and_wire_knowledge(
                self._app_state,
                provider_registry=registry,
                synthesis_failure_event=SETTINGS_SERVICE_SWAP_FAILED,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="knowledge_service",
                trigger=trigger,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            SETTINGS_SUBSCRIBER_NOTIFIED,
            subscriber=self.subscriber_name,
            trigger=trigger,
            note="knowledge service rebuilt and swapped",
        )
