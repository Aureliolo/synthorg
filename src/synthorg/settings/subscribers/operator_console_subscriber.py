"""Operator-console subscriber: rebuild the console on a live change.

Watches the ``chief_of_staff`` operator-console keys (the enable toggle, the
model, and the per-session bounds/tier) and rebuilds the console through the
fail-closed builder whenever one changes, so enabling, disabling, or
re-tuning the console takes effect with no restart.

The rebuild re-runs the same governance + MCP-self-consumer + model-bound gate
the startup wirer uses (:func:`build_operator_console`), so a live enable
materialises the console ONLY when the boot engine already carries security
governance and a model is bound; a toggle-on without either stays fail-closed
(the console stays ``None`` and a CONFIGURE turn 503s). Because the governance
re-check happens on every rebuild, the toggle need not be restart-bound.
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
        ("chief_of_staff", "operator_console_enabled"),
        ("chief_of_staff", "operator_console_model"),
        ("chief_of_staff", "operator_console_max_turns"),
        ("chief_of_staff", "operator_console_cost_ceiling_usd"),
        ("chief_of_staff", "operator_console_autonomy_level"),
    },
)


class OperatorConsoleSettingsSubscriber:
    """Rebuild the operator console when its settings change.

    Args:
        app_state: Application state holding the meta + runtime slices.
        settings_service: Settings service the config load reads from.
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
        """Return the ``(namespace, key)`` pairs this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logging."""
        return "operator-console-settings"

    async def on_settings_changed(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """Rebuild the fail-closed console from the current config."""
        from synthorg.api.lifecycle_helpers.conversational_console_wiring import (  # noqa: PLC0415
            rebuild_operator_console,
        )
        from synthorg.meta.config import (  # noqa: PLC0415
            load_self_improvement_config,
        )
        from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

        try:
            si_config = await load_self_improvement_config(self._settings_service)
            await rebuild_operator_console(self._app_state, si_config=si_config)
            wired = self._app_state.slice(MetaStateSlice).operator_console is not None
            if si_config.chief_of_staff.operator_console_enabled and not wired:
                logger.warning(
                    SETTINGS_SUBSCRIBER_NOTIFIED,
                    subscriber=self.subscriber_name,
                    namespace=namespace,
                    key=key,
                    note=(
                        "operator console enabled but stays inert: no security"
                        " governance / MCP self-consumer / boot engine wired, or"
                        " no operator_console_model selected. Wire an enabled"
                        " SecurityConfig + the MCP self-consumer and select a"
                        " console model to expose it."
                    ),
                )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="operator_console",
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
            note="operator console rebuilt",
        )
