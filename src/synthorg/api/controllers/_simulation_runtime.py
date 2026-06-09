"""Background-run lifecycle for simulation endpoints.

Holds the asynchronous execution machinery driven by
``SimulationController.start_simulation``: the runner coroutine, its
failure-marking helper, the done-callback wiring, and the rollback that
unwinds a partially-constructed start. Kept out of the controller module
so the request-handling surface stays focused on routing.
"""

import asyncio
import contextlib

from synthorg.api.state import AppState
from synthorg.client.config import SimulationRunnerConfig
from synthorg.client.runner import SimulationRunner
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.client.state import client_simulation_state_of
from synthorg.client.store import SimulationRecord
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.client import (
    SIMULATION_RUN_CANCELLED,
    SIMULATION_RUN_FAILED,
)
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


async def _mark_failed(
    sim_state: ClientSimulationState,
    simulation_id: str,
    error: str,
) -> None:
    """Mark a simulation run failed with a stable error message.

    Wraps ``simulation_store.update_status`` in ``contextlib.suppress``
    so a missing record (``KeyError``, race with cancellation) or an
    invalid status transition (``ValueError``) does not propagate; the
    failure has already been logged by the caller.
    """
    with contextlib.suppress(ValueError, KeyError):
        await sim_state.simulation_store.update_status(
            simulation_id,
            status="failed",
            error=error,
        )


def attach_runner_callbacks(
    task: asyncio.Task[None],
    *,
    sim_state: ClientSimulationState,
    simulation_id: str,
) -> None:
    """Wire the failure logger + background-task discard to a runner.

    The exception logger is registered FIRST so a task that finishes
    between ``create_task`` and the ``add`` below still has its
    failure surfaced -- asyncio invokes done-callbacks in the order
    they were registered. Adding the task to the set before attaching
    the logger would let a fast-completing failure fire ``discard``
    first and silently drop the error.
    """
    task.add_done_callback(
        log_task_exceptions(
            logger,
            SIMULATION_RUN_FAILED,
            simulation_id=simulation_id,
        ),
    )
    task.add_done_callback(sim_state.background_tasks.discard)
    sim_state.background_tasks.add(task)


async def rollback_register_if_absent(
    spawned_task: asyncio.Task[None] | None,
    *,
    sim_state: ClientSimulationState,
    record: SimulationRecord,
) -> None:
    """Tear down a partially-constructed simulation start.

    If the runner task was spawned before the post-claim setup raised,
    cancel and drain it before unregistering -- otherwise the orphan
    runner would race the unregister and either re-claim the
    ``simulation_id`` via ``update_status`` or silently corrupt the
    store. ``shield`` is unnecessary here because the caller is the
    request handler, not a coroutine guarding against external
    cancellation.

    Passes the claimed ``record`` to ``unregister`` so the
    compare-and-delete semantics protect a fresh retry that might have
    won the slot between the failure and this rollback running.
    """
    simulation_id = record.simulation_id
    if spawned_task is not None:
        spawned_task.cancel()
        try:
            await spawned_task
        except asyncio.CancelledError:
            pass
        except Exception as drain_exc:
            reraise_critical(drain_exc)
            logger.warning(
                SIMULATION_RUN_FAILED,
                simulation_id=simulation_id,
                stage="rollback_drain",
                error_type=type(drain_exc).__name__,
                error=safe_error_description(drain_exc),
            )
    await sim_state.simulation_store.unregister(simulation_id, expected=record)


async def run_in_background(
    *,
    app_state: AppState,
    record: SimulationRecord,
) -> None:
    """Execute a simulation run and update the store with results.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    sim_state = client_simulation_state_of(app_state)
    if sim_state.intake_engine is None:
        await _mark_failed(
            sim_state, record.simulation_id, "Intake engine not configured"
        )
        return
    clients = await sim_state.pool.list_clients()
    if not clients:
        await _mark_failed(sim_state, record.simulation_id, "No clients in pool")
        return
    resolver = config_resolver_of(app_state)
    try:
        task_timeout_sec = await resolver.get_float(
            "simulations", "task_timeout_seconds"
        )
        review_timeout_sec = await resolver.get_float(
            "simulations", "review_timeout_seconds"
        )
    except (SettingNotFoundError, ValueError) as exc:
        # Surface the specific simulation_id that aborted so the
        # broad ``except Exception`` below does not collapse this
        # path into "failed unexpectedly". Resolver already logged
        # the underlying lookup failure at WARNING.
        logger.warning(
            SIMULATION_RUN_FAILED,
            simulation_id=record.simulation_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            stage="config_resolution",
        )
        await _mark_failed(
            sim_state,
            record.simulation_id,
            "Simulation timeout configuration error",
        )
        return
    runner = SimulationRunner(
        config=SimulationRunnerConfig(
            max_concurrent_tasks=4,
            task_timeout_sec=task_timeout_sec,
            review_timeout_sec=review_timeout_sec,
        ),
        intake_engine=sim_state.intake_engine,
        feedback_sink=sim_state.feedback_store.record,
    )
    try:
        metrics, _ = await runner.run(
            sim_config=record.config,
            clients=clients,
        )
    except asyncio.CancelledError:
        logger.info(
            SIMULATION_RUN_CANCELLED,
            simulation_id=record.simulation_id,
        )
        await sim_state.simulation_store.update_status(
            record.simulation_id,
            status="cancelled",
        )
        raise
    except (ValueError, KeyError) as exc:
        logger.warning(
            SIMULATION_RUN_FAILED,
            simulation_id=record.simulation_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        await _mark_failed(
            sim_state, record.simulation_id, "Simulation configuration error"
        )
        return
    except Exception as exc:
        reraise_critical(exc)
        # Frame-locals on a simulation-run-failed path can carry the
        # entire simulation config; scrub + drop the traceback.
        logger.warning(
            SIMULATION_RUN_FAILED,
            simulation_id=record.simulation_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        await _mark_failed(
            sim_state, record.simulation_id, "Simulation failed unexpectedly"
        )
        return
    await sim_state.simulation_store.update_status(
        record.simulation_id,
        status="completed",
        metrics=metrics,
        progress=1.0,
    )
