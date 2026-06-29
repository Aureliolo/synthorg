"""Builder for the org-inflection monitor daemon.

Shared by the boot wiring (which builds + starts the monitor when alerts are
enabled at startup) and the alerts settings subscriber (which builds a fresh
instance when an operator enables alerts at runtime, including after a previous
monitor became unrestartable). Returns ``None`` when the signals facade is
absent, so the caller leaves the daemon unstarted.
"""

from synthorg.api.state import AppState
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.monitor import OrgInflectionMonitor
from synthorg.meta.state import MetaStateSlice


def build_org_inflection_monitor(
    app_state: AppState,
    *,
    cos_config: ChiefOfStaffConfig,
) -> OrgInflectionMonitor | None:
    """Build an org-inflection monitor bound to the wired signals facade.

    Args:
        app_state: Application state carrying the signals facade.
        cos_config: Chief-of-Staff config supplying the severity threshold
            and check interval.

    Returns:
        A fresh, unstarted :class:`OrgInflectionMonitor`, or ``None`` when no
        signals facade is wired (nothing to snapshot).
    """
    signals_service = app_state.slice(MetaStateSlice).signals_service
    if signals_service is None:
        return None
    from synthorg.meta.chief_of_staff.alerts import (  # noqa: PLC0415
        LoggingAlertSink,
        ProactiveAlertService,
    )
    from synthorg.meta.chief_of_staff.inflection import (  # noqa: PLC0415
        OrgInflectionDetector,
    )

    alert_service = ProactiveAlertService(
        alert_sinks=(LoggingAlertSink(),),
        severity_threshold=cos_config.inflection_severity_threshold,
    )
    return OrgInflectionMonitor(
        detector=OrgInflectionDetector(),
        snapshot_builder=signals_service.snapshot_builder,
        sinks=(alert_service,),
        check_interval_minutes=cos_config.inflection_check_interval_minutes,
    )


__all__ = ["build_org_inflection_monitor"]
