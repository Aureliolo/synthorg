"""Simulations settings subscriber: rebuilds the client-simulation runtime.

The client-simulation intake strategy, model, default project, review pipeline,
and verification stage (enabled / grader / decomposer) are baked into the
``ClientSimulationState`` at construction, and the multi-agent coordinator
captures the intake engine at assembly. A change to any of those keys therefore
goes live through the same ``reload_runtime_services`` path that a provider
reinit uses: it rebuilds the simulation state from the live settings DB AND the
coordinator around it, keeping them coherent. The reload is atomic per service,
so an in-flight task keeps its captured engine.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)

logger = get_logger(__name__)

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        ("simulations", "client_intake_enabled"),
        ("simulations", "intake_strategy"),
        ("simulations", "intake_model"),
        ("simulations", "intake_default_project"),
        ("simulations", "review_pipeline_strategy"),
        ("simulations", "verification_review_enabled"),
        ("simulations", "verification_grader"),
        ("simulations", "verification_decomposer"),
    }
)


class SimulationsSettingsSubscriber:
    """Rebuild the client-simulation runtime on a simulations-config change.

    On any watched key change, runs ``reload_runtime_services``, which rebuilds
    the ``ClientSimulationState`` from the live settings DB and the coordinator
    that captures its intake engine. Errors propagate to the dispatcher, which
    logs them with subscriber context; the previously wired runtime stays in
    place on failure.

    Args:
        app_state: Application state holding the runtime services.
    """

    def __init__(self, app_state: AppState) -> None:
        self._app_state = app_state

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return the simulations-namespace keys this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logging."""
        return "simulations-settings"

    async def on_settings_changed(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """Rebuild the client-simulation runtime from current settings.

        Args:
            namespace: Changed setting namespace.
            key: Changed setting key.
        """
        from synthorg.client.state import has_simulation_runtime  # noqa: PLC0415
        from synthorg.workers.runtime_builder import (  # noqa: PLC0415
            reload_runtime_services,
        )

        if not has_simulation_runtime(self._app_state):
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="no simulation runtime wired; nothing to rebuild",
            )
            return
        try:
            await reload_runtime_services(self._app_state)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="client_simulation_runtime",
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
            note="client simulation runtime rebuilt",
        )
