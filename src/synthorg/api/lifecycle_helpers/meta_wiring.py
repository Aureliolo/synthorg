# module-kind: orchestrator
"""On-startup wiring for the meta read-view facades.

``AnalyticsService`` and ``ReportsService`` are thin read-only projections
layered on top of the already-wired :class:`SignalsService`. Each hook is
best-effort + idempotent: an already-set slice field short-circuits, and a
missing dependency leaves the analytics / reports MCP handlers to 503 rather
than poisoning startup.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


async def _wire_analytics_service(app_state: AppState) -> None:
    """Wire the analytics read-view once the signals facade exists.

    Depends only on the wired ``SignalsService``; degrades to skipped (the
    ``synthorg_analytics_*`` / ``synthorg_metrics_*`` MCP tools 503) when
    signals is absent rather than raising.
    """
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

    if app_state.slice(MetaStateSlice).analytics_service is not None:
        return
    signals_service = app_state.slice(MetaStateSlice).signals_service
    if signals_service is None:
        logger.info(
            API_APP_STARTUP,
            service="analytics",
            note="signals service absent; analytics wiring skipped",
        )
        return
    from synthorg.meta.analytics.service import AnalyticsService  # noqa: PLC0415

    try:
        analytics_service = AnalyticsService(signals=signals_service)
        app_state.wire(MetaStateSlice, analytics_service=analytics_service)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="analytics",
            note="analytics wiring failed; MCP handlers will 503",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(API_APP_STARTUP, service="analytics", note="wired")


async def _wire_reports_service(app_state: AppState) -> None:
    """Wire the reports facade once the analytics read-view exists.

    Depends on ``AnalyticsService`` (wired just before this hook); degrades
    to skipped (the ``synthorg_reports_*`` MCP tools 503) when analytics is
    absent rather than raising.
    """
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

    if app_state.slice(MetaStateSlice).reports_service is not None:
        return
    analytics_service = app_state.slice(MetaStateSlice).analytics_service
    if analytics_service is None:
        logger.info(
            API_APP_STARTUP,
            service="reports",
            note="analytics service absent; reports wiring skipped",
        )
        return
    from synthorg.meta.reports.service import ReportsService  # noqa: PLC0415

    try:
        reports_service = ReportsService(analytics=analytics_service)
        app_state.wire(MetaStateSlice, reports_service=reports_service)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="reports",
            note="reports wiring failed; MCP handlers will 503",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(API_APP_STARTUP, service="reports", note="wired")


async def _wire_org_inflection_monitor(app_state: AppState) -> None:
    """Start the org-inflection monitor daemon behind ``alerts_enabled``.

    Best-effort + idempotent. Gated on the wired signals facade (shares its
    snapshot builder) and ``chief_of_staff.alerts_enabled``; the daemon emits
    detected inflections to the proactive alert sink. Stopped by the shutdown
    runner. A missing signals facade or a disabled flag leaves it unstarted.
    """
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    if app_state.slice(MetaStateSlice).org_inflection_monitor is not None:
        return
    signals_service = app_state.slice(MetaStateSlice).signals_service
    if signals_service is None:
        return
    from synthorg.meta.config import load_self_improvement_config  # noqa: PLC0415

    si_config = await load_self_improvement_config(
        app_state.slice(SettingsStateSlice).settings_service,
    )
    cos_config = si_config.chief_of_staff
    if not cos_config.alerts_enabled:
        logger.info(
            API_APP_STARTUP,
            service="org_inflection_monitor",
            note="alerts disabled; monitor not started",
        )
        return
    from synthorg.meta.chief_of_staff.alerts import (  # noqa: PLC0415
        LoggingAlertSink,
        ProactiveAlertService,
    )
    from synthorg.meta.chief_of_staff.inflection import (  # noqa: PLC0415
        OrgInflectionDetector,
    )
    from synthorg.meta.chief_of_staff.monitor import (  # noqa: PLC0415
        OrgInflectionMonitor,
    )

    try:
        alert_service = ProactiveAlertService(
            alert_sinks=(LoggingAlertSink(),),
            severity_threshold=cos_config.inflection_severity_threshold,
        )
        monitor = OrgInflectionMonitor(
            detector=OrgInflectionDetector(),
            snapshot_builder=signals_service.snapshot_builder,
            sinks=(alert_service,),
            check_interval_minutes=cos_config.inflection_check_interval_minutes,
        )
        await monitor.start()
        app_state.wire(MetaStateSlice, org_inflection_monitor=monitor)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="org_inflection_monitor",
            note="inflection monitor wiring failed; daemon not started",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(API_APP_STARTUP, service="org_inflection_monitor", note="wired")


async def _wire_analytics_collector(app_state: AppState) -> None:
    """Configure the cross-deployment analytics collector role.

    Best-effort. The collector is the receiver side of cross-deployment
    telemetry (the emitter is wired in the self-improvement service); it is
    built only when ``cross_deployment_analytics.collector_enabled`` is set,
    so the ``/meta/analytics/*`` routes resolve in the collector role and
    honestly 503 otherwise. Module-global config (no slice).
    """
    from synthorg.api.controllers.meta_analytics import (  # noqa: PLC0415
        configure_analytics_controller,
    )
    from synthorg.meta.config import load_self_improvement_config  # noqa: PLC0415
    from synthorg.meta.telemetry.factory import (  # noqa: PLC0415
        build_analytics_collector,
        build_recommender,
    )
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    si_config = await load_self_improvement_config(
        app_state.slice(SettingsStateSlice).settings_service,
    )
    try:
        collector = build_analytics_collector(si_config)
        if collector is None:
            logger.info(
                API_APP_STARTUP,
                service="analytics_collector",
                note="collector role disabled; routes will 503",
            )
            return
        analytics_cfg = si_config.cross_deployment_analytics
        configure_analytics_controller(
            collector,
            build_recommender(si_config),
            min_deployments_floor=analytics_cfg.min_deployments_for_pattern,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="analytics_collector",
            note="collector wiring failed; routes will 503",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(API_APP_STARTUP, service="analytics_collector", note="wired")
