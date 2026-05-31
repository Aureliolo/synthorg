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
    from synthorg.persistence.db_handle import (  # noqa: PLC0415
        postgres_pool,
        sqlite_connection,
    )
    from synthorg.persistence.sqlite.cost_forecast_repo import (  # noqa: PLC0415
        SQLiteCostForecastRepository,
    )
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    budget_config = BudgetConfig()
    benchmark_provider = StubBenchmarkScoreProvider()
    backend_name = persistence_of(app_state).backend_name
    if backend_name == "sqlite":
        forecast_repo: CostForecastRepository = SQLiteCostForecastRepository(
            sqlite_connection(persistence_of(app_state)),
            write_context=persistence_of(app_state).write_context,
            currency_getter=lambda: budget_config.currency,
        )
    else:
        from synthorg.persistence.postgres.cost_forecast_repo import (  # noqa: PLC0415
            PostgresCostForecastRepository,
        )

        forecast_repo = PostgresCostForecastRepository(
            postgres_pool(persistence_of(app_state)),
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

    Builds the live-activity ``CockpitService``, the flight-recorder
    query/seek service, and the steering directive, then installs them
    on ``AppState`` for the cockpit controllers and MCP tools. Requires
    a connected persistence backend (for the frame store) plus a task
    engine and interrupt store.
    """
    from synthorg.communication.state import (  # noqa: PLC0415
        CommunicationStateSlice,
    )
    from synthorg.engine.state import EngineStateSlice, task_engine_of  # noqa: PLC0415
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )

    interrupt_store = app_state.slice(CommunicationStateSlice).interrupt_store
    if (
        app_state.slice(PersistenceStateSlice).backend is None
        or app_state.slice(EngineStateSlice).task_engine is None
        or interrupt_store is None
    ):
        return
    from synthorg.engine.cockpit import CockpitService  # noqa: PLC0415
    from synthorg.engine.cockpit.state import CockpitStateSlice  # noqa: PLC0415
    from synthorg.engine.flight_recording import (  # noqa: PLC0415
        FlightRecorderService,
    )
    from synthorg.engine.intervention import build_steering_directive  # noqa: PLC0415

    frames = persistence_of(app_state).flight_recorder_frames
    cockpit_service = CockpitService(
        task_engine_of(app_state),
        frames,
        clock=app_state.clock,
    )
    flight_recorder_service = FlightRecorderService(frames)
    steering_directive = build_steering_directive(
        interrupt_store,
        clock=app_state.clock,
    )
    app_state.swap_slice(
        CockpitStateSlice(
            cockpit_service=cockpit_service,
            flight_recorder_service=flight_recorder_service,
            steering_directive=steering_directive,
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
]
