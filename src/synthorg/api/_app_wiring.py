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

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.persistence.cost_forecast_protocol import CostForecastRepository
    from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)


def _wire_cost_dial_services(app_state: AppState) -> None:
    """Wire the cost-dial services onto AppState behind a persistence guard.

    Builds the BudgetConfig, StubBenchmarkScoreProvider, the per-backend
    CostForecastRepository, the CostForecaster, and the ParetoAnalyzer
    then hot-swaps them onto AppState through the lock-protected
    ``swap_*`` methods so an in-flight controller read cannot race the
    boot wiring.
    """
    from synthorg.budget.benchmark_stub import (  # noqa: PLC0415
        StubBenchmarkScoreProvider,
    )
    from synthorg.budget.config import BudgetConfig  # noqa: PLC0415
    from synthorg.budget.forecaster import CostForecaster  # noqa: PLC0415
    from synthorg.budget.pareto import ParetoAnalyzer  # noqa: PLC0415
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.persistence.sqlite.cost_forecast_repo import (  # noqa: PLC0415
        SQLiteCostForecastRepository,
    )
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    budget_config = BudgetConfig()
    benchmark_provider = StubBenchmarkScoreProvider()
    backend_name = persistence_of(app_state).backend_name
    if backend_name == "sqlite":
        forecast_repo: CostForecastRepository = SQLiteCostForecastRepository(
            persistence_of(app_state).get_db(),
            write_context=persistence_of(app_state).write_context,
            currency_getter=lambda: budget_config.currency,
        )
    else:
        from synthorg.persistence.postgres.cost_forecast_repo import (  # noqa: PLC0415
            PostgresCostForecastRepository,
        )

        forecast_repo = PostgresCostForecastRepository(
            persistence_of(app_state).get_db(),
            currency_getter=lambda: budget_config.currency,
        )
    forecaster = CostForecaster(budget_config=budget_config)
    analyzer = ParetoAnalyzer(
        benchmark_provider=benchmark_provider,
        budget_config=budget_config,
    )
    app_state.wire(
        BudgetStateSlice,
        budget_config=budget_config,
        benchmark_provider=benchmark_provider,
        cost_forecast_repo=forecast_repo,
        cost_forecaster=forecaster,
        pareto_analyzer=analyzer,
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
    except Exception as exc:
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
    cockpit_service = CockpitService(
        task_engine_of(app_state),
        frames,
        clock=app_state.clock,
    )
    flight_recorder_service = FlightRecorderService(frames)
    app_state.swap_slice(
        CockpitStateSlice(
            cockpit_service=cockpit_service,
            flight_recorder_service=flight_recorder_service,
        )
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
    except Exception as exc:
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
    from synthorg.engine.intervention import (  # noqa: PLC0415
        SteeringService,
        build_supersession_proposer,
    )
    from synthorg.engine.state import (  # noqa: PLC0415
        EngineStateSlice,
        task_engine_of,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )
    from synthorg.project_brain.state import ProjectBrainStateSlice  # noqa: PLC0415
    from synthorg.settings.state import (  # noqa: PLC0415
        SettingsStateSlice,
        settings_service_of,
    )

    if app_state.slice(CockpitStateSlice).steering_service is not None:
        return
    brain_service = app_state.slice(ProjectBrainStateSlice).service
    if (
        brain_service is None
        or app_state.slice(PersistenceStateSlice).backend is None
        or app_state.slice(EngineStateSlice).task_engine is None
    ):
        return
    enabled = False
    model: str | None = None
    provider = None
    if (
        app_state.slice(SettingsStateSlice).settings_service is not None
        and provider_registry is not None
    ):
        settings = settings_service_of(app_state)
        enabled = (
            await settings.get("cockpit", "steering_proposer_enabled")
        ).value.strip().lower() == "true"
        model = (
            await settings.get("cockpit", "steering_proposer_model")
        ).value.strip() or None
        names = provider_registry.list_providers()
        provider = provider_registry.get(names[0]) if names else None
    app_state.wire(
        CockpitStateSlice,
        steering_service=SteeringService(
            brain_service=brain_service,
            brain_repo=persistence_of(app_state).project_brain,
            task_engine=task_engine_of(app_state),
            proposer=build_supersession_proposer(
                provider, model=model, enabled=enabled
            ),
            clock=app_state.clock,
        ),
    )
    logger.info(API_APP_STARTUP, service="steering", note="wired")


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


__all__ = [
    "_try_wire_cockpit",
    "_try_wire_cost_dial",
    "_wire_cockpit_services",
    "_wire_cost_dial_services",
    "_wire_environment_service",
    "_wire_steering_service",
]
