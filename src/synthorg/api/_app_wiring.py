"""Best-effort post-startup wiring helpers for optional subsystems.

Extracted from :mod:`synthorg.api.app` so the application factory module
stays focused on construction + lifecycle and the god-module gate
(:mod:`scripts.check_no_growth_in_god_modules`) keeps net-shrinking.

Each ``_try_wire_*`` helper is invoked from the on-startup hook chain
in :mod:`synthorg.api.lifecycle_builder` once persistence has connected.
The helpers are intentionally idempotent (early-return when the
service is already wired) and never poison startup (the broad-except
funnels through :func:`reraise_critical` then logs and swallows).
"""

from typing import TYPE_CHECKING

from synthorg.api._benchmark_wiring import (
    build_benchmark_score_repo,
    select_benchmark_provider,
)
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.cost_forecast_protocol import CostForecastRepository
from synthorg.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from synthorg.budget.config import BudgetConfig
    from synthorg.budget.enforcer import BudgetEnforcer
    from synthorg.budget.forecast_history import CostTrackerHistoryLookup
    from synthorg.budget.pareto_assignments import AgentRegistryAssignmentLookup
    from synthorg.budget.tracker import CostTracker
    from synthorg.engine.intervention import SteeringSupersessionProposer

logger = get_logger(__name__)


def _build_cost_forecast_repo(
    app_state: AppState,
    budget_config: BudgetConfig,
) -> CostForecastRepository:
    """Build the per-backend cost-forecast repository.

    Returns:
        The SQLite or Postgres cost-forecast repository for the backend.
    """
    from synthorg.persistence.backend_dispatch import (  # noqa: PLC0415
        build_for_backend,
    )
    from synthorg.persistence.db_handle import (  # noqa: PLC0415
        postgres_pool,
        sqlite_connection,
    )
    from synthorg.persistence.sqlite.cost_forecast_repo import (  # noqa: PLC0415
        SQLiteCostForecastRepository,
    )
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    persistence = persistence_of(app_state)

    def _sqlite() -> CostForecastRepository:
        return SQLiteCostForecastRepository(
            sqlite_connection(persistence),
            write_context=persistence.write_context,
            currency_getter=lambda: budget_config.currency,
        )

    def _postgres() -> CostForecastRepository:
        from synthorg.persistence.postgres.cost_forecast_repo import (  # noqa: PLC0415
            PostgresCostForecastRepository,
        )

        return PostgresCostForecastRepository(
            postgres_pool(persistence),
            currency_getter=lambda: budget_config.currency,
        )

    return build_for_backend(persistence, sqlite=_sqlite, postgres=_postgres)


def _build_pareto_inputs(
    app_state: AppState,
) -> tuple[
    AgentRegistryAssignmentLookup | None,
    CostTrackerHistoryLookup | None,
    CostTracker | None,
]:
    """Resolve the live roster + spend lookups for the Pareto frontier.

    Sources the frontier and the forecaster's history from the live roster
    and observed spend so they render real downgrade candidates / warm
    forecasts instead of empty defaults. Also attaches the durable
    project-cost write + restart-safe dedup repos onto the cost tracker now
    that persistence is connected (the tracker is built at the synchronous
    construction phase before a backend exists; the dedup guard makes the
    increment idempotent across a JetStream redelivery after a restart). A
    registry/tracker absent at wiring time leaves both lookups ``None``
    (cold-start forecaster, empty-frontier analyzer) rather than poisoning
    startup.

    Returns:
        ``(assignment_lookup, history_lookup, cost_tracker)``.
    """
    from synthorg.budget.forecast_history import (  # noqa: PLC0415
        CostTrackerHistoryLookup,
    )
    from synthorg.budget.pareto_assignments import (  # noqa: PLC0415
        AgentRegistryAssignmentLookup,
    )
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.hr.state import HrStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    persistence = persistence_of(app_state)
    registry = app_state.slice(HrStateSlice).agent_registry
    cost_tracker = app_state.slice(BudgetStateSlice).cost_tracker
    if cost_tracker is not None:
        cost_tracker.attach_durable_repos(
            project_cost_repo=persistence.project_cost_aggregates,
            claim_seen_repo=persistence.project_cost_claim_seen,
        )
    if registry is None or cost_tracker is None:
        return None, None, cost_tracker
    assignment_lookup = AgentRegistryAssignmentLookup(
        registry=registry,
        cost_tracker=cost_tracker,
        clock=app_state.clock.now,
    )
    history_lookup = CostTrackerHistoryLookup(
        registry=registry,
        cost_tracker=cost_tracker,
        clock=app_state.clock.now,
    )
    return assignment_lookup, history_lookup, cost_tracker


def _build_budget_enforcer(
    app_state: AppState,
    budget_config: BudgetConfig,
    cost_tracker: CostTracker | None,
) -> BudgetEnforcer | None:
    """Build the shared budget enforcer the replan gate consults.

    Needs the live cost tracker; a persistence-less / tracker-absent boot
    leaves it ``None`` and the magentic replan hook skips the affordability
    check.

    Returns:
        The wired ``BudgetEnforcer`` or ``None`` when no cost tracker.
    """
    from synthorg.budget.enforcer import BudgetEnforcer  # noqa: PLC0415
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.notifications.state import (  # noqa: PLC0415
        NotificationsStateSlice,
    )

    if cost_tracker is None:
        return None
    budget_slice = app_state.slice(BudgetStateSlice)
    return BudgetEnforcer(
        budget_config=budget_config,
        cost_tracker=cost_tracker,
        quota_tracker=budget_slice.quota_tracker,
        risk_tracker=budget_slice.risk_tracker,
        notification_dispatcher=app_state.slice(NotificationsStateSlice).dispatcher,
    )


def _wire_cost_dial_services(app_state: AppState) -> None:
    """Wire the cost-dial services onto AppState behind a persistence guard.

    Builds the BudgetConfig, the per-backend CostForecastRepository +
    BenchmarkScoreRepository, the benchmark-score provider selected by the
    ``budget.benchmark_provider`` discriminator (stub by default, measured
    behind the repo with a stub fallback), the CostForecaster, and the
    ParetoAnalyzer then hot-swaps them onto AppState through the
    lock-protected ``swap_*`` methods so an in-flight controller read
    cannot race the boot wiring.
    """
    from synthorg.budget.config import BudgetConfig  # noqa: PLC0415
    from synthorg.budget.forecast_service import (  # noqa: PLC0415
        BudgetForecastService,
    )
    from synthorg.budget.forecaster import CostForecaster  # noqa: PLC0415
    from synthorg.budget.model_tier import ModelTierMap  # noqa: PLC0415
    from synthorg.budget.pareto import ParetoAnalyzer  # noqa: PLC0415
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415

    budget_config = BudgetConfig()
    forecast_repo = _build_cost_forecast_repo(app_state, budget_config)
    benchmark_score_repo = build_benchmark_score_repo(app_state)
    model_tier_map = ModelTierMap(overrides=budget_config.model_tier_overrides)
    benchmark_provider = select_benchmark_provider(
        budget_config.benchmark_provider,
        repo=benchmark_score_repo,
        tier_map=model_tier_map,
    )
    assignment_lookup, history_lookup, cost_tracker = _build_pareto_inputs(app_state)
    forecaster = CostForecaster(
        budget_config=budget_config,
        history_lookup=history_lookup,
        clock=app_state.clock.now,
    )
    analyzer = ParetoAnalyzer(
        benchmark_provider=benchmark_provider,
        budget_config=budget_config,
        assignment_lookup=assignment_lookup,
        model_tier_map=model_tier_map,
    )
    forecast_service = BudgetForecastService(
        repo=forecast_repo,
        forecaster=forecaster,
        budget_config=budget_config,
        clock=app_state.clock.now,
    )
    budget_enforcer = _build_budget_enforcer(app_state, budget_config, cost_tracker)
    app_state.wire(
        BudgetStateSlice,
        budget_config=budget_config,
        benchmark_provider=benchmark_provider,
        benchmark_score_repo=benchmark_score_repo,
        cost_forecast_repo=forecast_repo,
        cost_forecaster=forecaster,
        pareto_analyzer=analyzer,
        forecast_service=forecast_service,
        budget_enforcer=budget_enforcer,
    )


def _try_wire_performance_persistence(app_state: AppState) -> None:
    """Attach the durable metric repos to the performance tracker.

    Best-effort and idempotent: the tracker is built at the construction
    phase before persistence is connected, so its task/collaboration
    metric repos are attached here once a backend exists. A persistence-
    less boot (tests/dev) leaves the tracker in-memory-only.
    """
    from synthorg.hr.state import HrStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )

    tracker = app_state.slice(HrStateSlice).performance_tracker
    if tracker is None or app_state.slice(PersistenceStateSlice).backend is None:
        return
    try:
        persistence = persistence_of(app_state)
        tracker.attach_metric_repos(
            task_metric_repo=persistence.task_metrics,
            collab_metric_repo=persistence.collaboration_metrics,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="performance_persistence",
            note="performance-metric persistence wiring failed; in-memory only",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


def _try_wire_cost_dial(app_state: AppState) -> None:
    """Wire the cost-dial services best-effort; never poison startup."""
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
    )

    forecaster = app_state.slice(BudgetStateSlice).cost_forecaster
    if app_state.slice(PersistenceStateSlice).backend is None or forecaster is not None:
        return
    try:
        _wire_cost_dial_services(app_state)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="cost_dial",
            note="cost-dial wiring failed; controllers will 503",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


def _wire_cockpit_services(app_state: AppState) -> None:
    """Construct the mission-control cockpit services from live state.

    Builds the live-activity ``CockpitService`` and the flight-recorder
    query/seek service, then installs them on ``AppState`` for the
    cockpit controllers and MCP tools. Requires a connected persistence
    backend (for the frame store) plus a task engine. The steering
    service wires separately in ``_wire_steering_service`` once the
    project brain is up (it records directives through the brain).
    """
    from synthorg.engine.state import EngineStateSlice, task_engine_of  # noqa: PLC0415
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
        red_team_reports_of,
    )

    if (
        app_state.slice(PersistenceStateSlice).backend is None
        or app_state.slice(EngineStateSlice).task_engine is None
    ):
        return
    from synthorg.engine.cockpit import CockpitService  # noqa: PLC0415
    from synthorg.engine.cockpit.state import CockpitStateSlice  # noqa: PLC0415
    from synthorg.engine.flight_recording import (  # noqa: PLC0415
        FlightRecorderService,
    )

    frames = persistence_of(app_state).flight_recorder_frames
    # Partial wire (not swap_slice) so the ``steering_notifier`` wired at
    # construction (where the channels plugin lives) and any later
    # ``steering_service`` survive this hook.
    app_state.wire(
        CockpitStateSlice,
        cockpit_service=CockpitService(
            task_engine_of(app_state),
            frames,
            clock=app_state.clock,
        ),
        flight_recorder_service=FlightRecorderService(
            frames,
            red_team_reports=red_team_reports_of(app_state),
        ),
    )


def _try_wire_cockpit(app_state: AppState) -> None:
    """Wire the cockpit services best-effort; never poison startup."""
    from synthorg.engine.cockpit.state import (  # noqa: PLC0415
        CockpitStateSlice,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
    )

    if (
        app_state.slice(PersistenceStateSlice).backend is None
        or app_state.slice(CockpitStateSlice).cockpit_service is not None
    ):
        return
    try:
        _wire_cockpit_services(app_state)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="cockpit",
            note="cockpit wiring failed; controllers will 503",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_steering_service(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
) -> None:
    """Wire the mid-flight steering service once the project brain is up.

    Runs after ``_wire_project_brain`` in the feature-wiring chain because the
    directive write path records through ``ProjectBrainService`` (memory-gated),
    while the loop read path (the steering inbox in the boot ``AgentEngine``) is
    persistence-only and already wired earlier. Idempotent: a steering service
    already on the cockpit slice short-circuits. The pluggable supersession
    proposer is built behind ``cockpit.steering_proposer_enabled`` plus a model
    id; a missing provider or disabled flag degrades it to the no-op proposer. A
    missing brain leaves the steering controllers + MCP tools to 503 rather than
    poisoning startup.
    """
    from synthorg.engine.cockpit.state import CockpitStateSlice  # noqa: PLC0415
    from synthorg.engine.intervention import SteeringService  # noqa: PLC0415
    from synthorg.engine.state import (  # noqa: PLC0415
        EngineStateSlice,
        task_engine_of,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )
    from synthorg.project_brain.state import ProjectBrainStateSlice  # noqa: PLC0415

    if app_state.slice(CockpitStateSlice).steering_service is not None:
        return
    brain_service = app_state.slice(ProjectBrainStateSlice).service
    if (
        brain_service is None
        or app_state.slice(PersistenceStateSlice).backend is None
        or app_state.slice(EngineStateSlice).task_engine is None
    ):
        return
    proposer = await _build_steering_proposer(
        app_state, provider_registry=provider_registry
    )
    app_state.wire(
        CockpitStateSlice,
        steering_service=SteeringService(
            brain_service=brain_service,
            brain_repo=persistence_of(app_state).project_brain,
            task_engine=task_engine_of(app_state),
            proposer=proposer,
            notifier=app_state.slice(CockpitStateSlice).steering_notifier,
            clock=app_state.clock,
        ),
    )
    logger.info(API_APP_STARTUP, service="steering", note="wired")


async def _build_steering_proposer(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
) -> SteeringSupersessionProposer:
    """Build the supersession proposer from cockpit.steering_proposer_* config.

    The proposer is LLM-backed only when the feature flag, a provider, and a
    model id are all present; otherwise it degrades to the no-op proposer.

    Returns:
        The selected ``SteeringSupersessionProposer``.
    """
    from synthorg.engine.intervention import (  # noqa: PLC0415
        build_supersession_proposer,
    )
    from synthorg.settings.state import (  # noqa: PLC0415
        SettingsStateSlice,
        config_resolver_of,
        settings_service_of,
    )

    enabled = False
    model: str | None = None
    provider = None
    if (
        app_state.slice(SettingsStateSlice).settings_service is not None
        and provider_registry is not None
    ):
        settings = settings_service_of(app_state)
        enabled = await config_resolver_of(app_state).get_bool(
            "cockpit", "steering_proposer_enabled"
        )
        model = (
            await settings.get("cockpit", "steering_proposer_model")
        ).value.strip() or None
        names = provider_registry.list_providers()
        configured = (
            await settings.get("cockpit", "steering_proposer_provider")
        ).value.strip()
        if configured and configured in names:
            provider = provider_registry.get(configured)
        elif names:
            provider = provider_registry.get(names[0])
    return build_supersession_proposer(provider, model=model, enabled=enabled)


def _wire_environment_service(app_state: AppState) -> None:
    """Wire the per-project reproducible-environment substrate.

    The declaration strategy is config-selected (manifest default); the
    service provisions the committed declaration into each project tree
    so the sandbox builds from the same declaration a fresh clone gets.
    Persistence-less boots skip wiring -- the service is optional and
    gated on ``has_environment_service`` downstream.
    """
    from synthorg.engine.workspace.state import (  # noqa: PLC0415
        WorkspaceStateSlice,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )

    environment_service = app_state.slice(WorkspaceStateSlice).environment_service
    if (
        app_state.slice(PersistenceStateSlice).backend is None
        or environment_service is not None
    ):
        return
    from synthorg.engine.workspace.environment import (  # noqa: PLC0415
        EnvironmentConfig,
        EnvironmentDeps,
        GitWorkspaceCommitter,
        build_environment_strategy,
    )
    from synthorg.engine.workspace.environment.service import (  # noqa: PLC0415
        EnvironmentService,
    )

    environment_config = EnvironmentConfig()
    app_state.wire(
        WorkspaceStateSlice,
        environment_service=EnvironmentService(
            repo=persistence_of(app_state).project_environments,
            strategy=build_environment_strategy(
                environment_config,
                EnvironmentDeps(clock=app_state.clock),
            ),
            config=environment_config,
            committer=GitWorkspaceCommitter(),
            clock=app_state.clock,
        ),
    )


def _try_wire_environment_service(app_state: AppState) -> None:
    """Wire the environment substrate best-effort; never poison startup."""
    try:
        _wire_environment_service(app_state)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="environment",
            note="environment wiring failed; controllers will 503",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


__all__ = [
    "_try_wire_cockpit",
    "_try_wire_cost_dial",
    "_try_wire_environment_service",
    "_try_wire_performance_persistence",
    "_wire_cockpit_services",
    "_wire_cost_dial_services",
    "_wire_environment_service",
    "_wire_steering_service",
]
