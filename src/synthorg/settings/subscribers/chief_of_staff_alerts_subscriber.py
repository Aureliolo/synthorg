"""Chief-of-Staff alerts settings subscriber.

Starts or stops the org-inflection alerts daemon live when an operator
toggles ``chief_of_staff.alerts_enabled`` or the persona master switch
``self_improvement.chief_of_staff_enabled`` (the kill-switch idiom for a
long-running daemon). The monitor exposes idempotent ``start()`` / ``stop()``;
a timed-out stop marks it unrestartable, so re-enabling builds a fresh
instance rather than restarting the dead one.
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
        ("chief_of_staff", "alerts_enabled"),
        ("self_improvement", "chief_of_staff_enabled"),
    }
)


class ChiefOfStaffAlertsSettingsSubscriber:
    """Reconcile the org-inflection monitor with the live alerts capability.

    Args:
        app_state: Application state owning the monitor slice + resolver.
        settings_service: Settings service used to reload the Chief-of-Staff
            config when building a fresh monitor.
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
        """Human-readable subscriber name for logs."""
        return "chief-of-staff-alerts"

    async def on_settings_changed(self, namespace: str, key: str) -> None:
        """Start or stop the alerts daemon to match the live capability."""
        from synthorg.meta.chief_of_staff._capability_gate import (  # noqa: PLC0415
            resolve_cos_autonomous_cap,
        )
        from synthorg.meta.config import (  # noqa: PLC0415
            load_self_improvement_config,
        )
        from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

        try:
            resolver = self._app_state.slice(SettingsStateSlice).config_resolver
            # The configured master + cap are the resolver-outage fallbacks, so
            # a transient resolver failure cannot suppress an enabled alerts
            # daemon (nor resume a disabled persona's).
            cfg = await load_self_improvement_config(self._settings_service)
            effective = await resolve_cos_autonomous_cap(
                resolver=resolver,
                key="alerts_enabled",
                master_fallback=cfg.chief_of_staff_enabled,
                cap_fallback=cfg.chief_of_staff.alerts_enabled,
            )
            if effective:
                await self._ensure_running()
            else:
                await self._ensure_stopped()
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note=f"alerts monitor {'started' if effective else 'stopped'}",
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="chief_of_staff_alerts",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    async def _ensure_running(self) -> None:
        """Start the wired monitor, rebuilding a fresh one if it is dead."""
        from synthorg.meta.chief_of_staff.monitor import (  # noqa: PLC0415
            InflectionMonitorLifecycleError,
        )
        from synthorg.meta.chief_of_staff.monitor_builder import (  # noqa: PLC0415
            build_org_inflection_monitor,
        )
        from synthorg.meta.config import (  # noqa: PLC0415
            load_self_improvement_config,
        )
        from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

        existing = self._app_state.slice(MetaStateSlice).org_inflection_monitor
        if existing is not None:
            try:
                await existing.start()  # idempotent when already running
            except InflectionMonitorLifecycleError:
                # A prior timed-out stop left it unrestartable; build fresh.
                pass
            else:
                return
        si_config = await load_self_improvement_config(self._settings_service)
        monitor = build_org_inflection_monitor(
            self._app_state, cos_config=si_config.chief_of_staff
        )
        if monitor is None:
            return
        # Wire BEFORE start so a running daemon is always tracked for shutdown.
        self._app_state.wire(MetaStateSlice, org_inflection_monitor=monitor)
        await monitor.start()

    async def _ensure_stopped(self) -> None:
        """Stop the wired monitor; a clean stop leaves it restartable."""
        from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

        existing = self._app_state.slice(MetaStateSlice).org_inflection_monitor
        if existing is not None:
            await existing.stop()


__all__ = ["ChiefOfStaffAlertsSettingsSubscriber"]
