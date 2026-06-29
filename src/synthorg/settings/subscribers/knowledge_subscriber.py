"""Knowledge settings subscriber: rebuilds the synthesis arm on change.

The knowledge synthesiser bakes its model, provider, strategy, and max-chunks
into the ``KnowledgeService`` at construction. A change to any of those keys
only goes live through a rebuild, so this subscriber re-runs the knowledge
wiring factory (which rebuilds the synthesiser and the service around it) and
atomically swaps the new service onto the knowledge state slice. In-flight
``ask`` / ``search`` calls hold the previously-captured service, so a swap never
disrupts a running query.

The master switch ``knowledge.enabled`` and the ``/ask`` gate
``knowledge.synthesis_enabled`` are NOT watched here: both are enforced live per
request at the knowledge MCP handlers, so they need no rebuild.
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
        ("knowledge", "synthesis_model"),
        ("knowledge", "synthesis_provider"),
        ("knowledge", "synthesis_synthesizer"),
        ("knowledge", "synthesis_max_chunks"),
    }
)


class KnowledgeSettingsSubscriber:
    """Rebuild + swap the knowledge service on a synthesis-config change.

    On any watched key change, resolves the live provider registry and re-runs
    the knowledge wiring factory, which rebuilds the synthesiser and the
    ``KnowledgeService`` around it and swaps the fresh service onto
    ``KnowledgeStateSlice``.

    Errors during rebuild propagate to the dispatcher, which logs them with full
    subscriber context and continues; the previously wired service stays in
    place.

    Args:
        app_state: Application state holding the slices + service swap surface.
        settings_service: Settings service held for parity with peer
            subscribers (the wiring factory reads settings off ``app_state``).
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
        """Return the knowledge-namespace keys this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logging."""
        return "knowledge-settings"

    async def on_settings_changed(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """Rebuild the knowledge service from current settings and swap it in.

        Args:
            namespace: Changed setting namespace.
            key: Changed setting key.
        """
        from synthorg.api.lifecycle_helpers.knowledge_wiring import (  # noqa: PLC0415
            _build_and_wire_knowledge,
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
            await _build_and_wire_knowledge(
                self._app_state,
                provider_registry=registry,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.error(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="knowledge_service",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
