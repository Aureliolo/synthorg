# module-kind: code
"""Boot wiring for the periodic model-refresh subsystem.

Wired only when ``providers.model_refresh_mode`` is not ``off`` AND a
provider management service is built AND persistence is connected (the
recommendation store is durable). Disabled by default, so a normal boot
skips this entirely. Mirrors ``wire_toolsmith``: the scheduler is started
BEFORE the AppState slice is published and rolled back on failure, and the
cadence scheduler is only started for the cadence modes
(``detect_only`` / ``reconcile_recommend``); ``manual_only`` wires the
on-demand service without a scheduler.
"""

from synthorg.api.api_core_state import org_mutation_service_of
from synthorg.api.services.upgrade_recommendation_service import (
    UpgradeRecommendationService,
)
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.persistence.upgrade_recommendation_factory import (
    build_upgrade_recommendation_repo,
)
from synthorg.providers.management.live_discovery_probe import LiveDiscoveryProbe
from synthorg.providers.management.model_refresh_service import ModelRefreshService
from synthorg.providers.management.refresh_config import (
    RefreshMode,
    load_model_refresh_config,
)
from synthorg.providers.management.refresh_scheduler import ModelRefreshScheduler
from synthorg.providers.management.refresh_state import ModelRefreshStateSlice
from synthorg.providers.management.upgrade_recommender import UpgradeRecommender
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)

_CADENCE_MODES: frozenset[RefreshMode] = frozenset(
    {RefreshMode.DETECT_ONLY, RefreshMode.RECONCILE_RECOMMEND},
)


async def wire_model_refresh(app_state: AppState) -> None:
    """Wire the model-refresh service + scheduler at startup when enabled.

    Idempotent for re-entered lifespans (shared-app fixtures): returns
    early when a service is already wired.
    """
    if app_state.slice(ModelRefreshStateSlice).service is not None:
        return
    resolver = app_state.slice(SettingsStateSlice).config_resolver
    if resolver is None:
        # Settings not yet wired (minimal-app boot); refresh stays off.
        return
    config = await load_model_refresh_config(resolver)
    if config.mode is RefreshMode.OFF:
        return

    management = app_state.slice(ProvidersStateSlice).management
    backend = app_state.slice(PersistenceStateSlice).backend
    if management is None or backend is None:
        logger.warning(
            API_APP_STARTUP,
            service="model_refresh",
            note="management or persistence absent; refresh disabled",
        )
        return
    repo = build_upgrade_recommendation_repo(backend)
    if repo is None:
        logger.warning(
            API_APP_STARTUP,
            service="model_refresh",
            note="recommendation store unavailable; refresh disabled",
        )
        return

    service = ModelRefreshService(
        mgmt_service=management,
        probe=LiveDiscoveryProbe(discovery=management),
        recommender=UpgradeRecommender(),
        repo=repo,
        config_resolver=resolver,
    )

    if config.mode not in _CADENCE_MODES:
        # manual_only: on-demand refresh, no cadence scheduler. Auto-apply
        # never runs here, so the org-mutation-coupled recommendation
        # service is deliberately not built (keeps manual_only boot from
        # depending on org-mutation wiring).
        app_state.swap_slice(
            ModelRefreshStateSlice(service=service, recommendation_repo=repo),
        )
        return

    # Cadence modes can auto-apply, which reassigns pinned agents through
    # the org-mutation service; build it only on this path.
    recommendation_service = UpgradeRecommendationService(
        repo=repo,
        org_mutations=org_mutation_service_of(app_state),
    )
    scheduler = ModelRefreshScheduler(
        service,
        interval_seconds=config.interval_seconds,
        config_resolver=resolver,
        apply_recommendation=recommendation_service.apply_auto,
    )
    try:
        await scheduler.start()
        app_state.swap_slice(
            ModelRefreshStateSlice(
                service=service,
                scheduler=scheduler,
                recommendation_repo=repo,
            ),
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        try:
            await scheduler.stop()
        except Exception as stop_exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(stop_exc)
            logger.warning(
                API_APP_STARTUP,
                service="model_refresh",
                note="scheduler rollback-stop failed",
                error_type=type(stop_exc).__name__,
                error=safe_error_description(stop_exc),
            )
        logger.warning(
            API_APP_STARTUP,
            service="model_refresh",
            note="scheduler start failed; refresh disabled",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


__all__ = ["wire_model_refresh"]
