# module-kind: code
"""Boot wiring for the closed-loop evaluation coordinator.

Constructs :class:`EvalLoopCoordinator` from the performance tracker +
training service once those collaborators are wired (its other deps -- an
:class:`EvaluationService`, :class:`TrajectoryScorer`,
:class:`DogfoodingDatasetBuilder`, and an empty
:class:`ExternalBenchmarkRegistry` -- are built here from the same tracker),
then publishes it on :class:`HrStateSlice` so the coordinator is live at boot
rather than dead.

The periodic :class:`EvalLoopCycleScheduler` that drives ``run_cycle`` on a
cadence is OPT-IN: a cycle can route corrective actions to the training
pipeline, so the background driver only starts when
``hr.eval_loop_cycle_enabled`` is set. When disabled the coordinator is still
published (operators can trigger cycles via the API); only the unattended
driver stays dormant.

Gated on a wired tracker + training service; without them the coordinator
stays absent and its consumers honestly 503.
"""

from datetime import timedelta
from typing import Final

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.trajectory.scorer import TrajectoryScorer
from synthorg.hr.evaluation.cycle_scheduler import EvalLoopCycleScheduler
from synthorg.hr.evaluation.dogfooding_dataset_builder import DogfoodingDatasetBuilder
from synthorg.hr.evaluation.evaluator import EvaluationService
from synthorg.hr.evaluation.external_benchmark_registry import ExternalBenchmarkRegistry
from synthorg.hr.evaluation.loop_coordinator import EvalLoopCoordinator
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import parse_bool, parse_float
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)

_SECONDS_PER_HOUR: Final[float] = 3600.0


async def wire_eval_loop(app_state: AppState) -> None:
    """Wire the evaluation-loop coordinator + opt-in cycle scheduler.

    Idempotent for re-entered lifespans: returns early when the coordinator is
    already wired.

    Args:
        app_state: The application state holding the collaborator slices.
    """
    hr = app_state.slice(HrStateSlice)
    if hr.eval_loop_coordinator is not None:
        return
    if hr.performance_tracker is None or hr.training_service is None:
        logger.warning(
            API_APP_STARTUP,
            service="eval_loop",
            note="tracker or training service absent; eval loop disabled",
        )
        return

    config_resolver = app_state.slice(SettingsStateSlice).config_resolver
    coordinator = EvalLoopCoordinator(
        performance_tracker=hr.performance_tracker,
        evaluation_service=EvaluationService(
            tracker=hr.performance_tracker,
            config_resolver=config_resolver,
        ),
        trajectory_scorer=TrajectoryScorer(),
        training_service=hr.training_service,
        dataset_builder=DogfoodingDatasetBuilder(
            performance_tracker=hr.performance_tracker,
        ),
        benchmark_registry=ExternalBenchmarkRegistry(),
        clock=app_state.clock,
    )

    enabled = bool(
        resolve_init_value(
            SettingNamespace.HR,
            "eval_loop_cycle_enabled",
            parse=parse_bool,
        ).value
    )
    if not enabled:
        app_state.wire(HrStateSlice, eval_loop_coordinator=coordinator)
        logger.info(
            API_APP_STARTUP,
            service="eval_loop",
            note="coordinator wired; cycle scheduler opt-in (disabled)",
        )
        return

    interval_seconds = float(
        resolve_init_value(
            SettingNamespace.HR,
            "eval_loop_cycle_interval_seconds",
            parse=parse_float,
        ).value
    )
    window_hours = float(
        resolve_init_value(
            SettingNamespace.HR,
            "eval_loop_cycle_window_hours",
            parse=parse_float,
        ).value
    )
    scheduler = EvalLoopCycleScheduler(
        coordinator,
        interval_seconds=interval_seconds,
        window=timedelta(seconds=window_hours * _SECONDS_PER_HOUR),
        config_resolver=config_resolver,
    )
    try:
        await scheduler.start()
        app_state.wire(
            HrStateSlice,
            eval_loop_coordinator=coordinator,
            eval_loop_cycle_scheduler=scheduler,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        try:
            await scheduler.stop()
        except Exception as stop_exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(stop_exc)
            logger.warning(
                API_APP_STARTUP,
                service="eval_loop",
                note="scheduler rollback-stop failed",
                error_type=type(stop_exc).__name__,
                error=safe_error_description(stop_exc),
            )
        # The coordinator is still valuable for manual cycles even when the
        # unattended driver failed to start, so publish it without the
        # scheduler rather than leaving the whole subsystem dead.
        app_state.wire(HrStateSlice, eval_loop_coordinator=coordinator)
        logger.warning(
            API_APP_STARTUP,
            service="eval_loop",
            note="scheduler start failed; coordinator wired without driver",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(API_APP_STARTUP, service="eval_loop", note="wired")


__all__ = ["wire_eval_loop"]
