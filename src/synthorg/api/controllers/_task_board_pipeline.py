# module-kind: code
"""The detached run behind a board filing, and how it is kept alive.

Filing from the board answers ``202`` and drives the work pipeline afterwards,
so the run outlives the handler's scope by design. Both halves live here
together because they are one arrangement: the spawn holds a strong reference
so the task is not collected mid-flight, attaches the exception logger BEFORE
the set-discard so a fast failure still surfaces, and the run itself decides
which failures are normal outcomes and which are defects.
"""

import asyncio

from synthorg.client.simulation_state import ClientSimulationState
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.pipeline.entry.task_board_adapter import (
    TaskBoardEntryAdapter,
    TaskBoardFiling,
)
from synthorg.engine.pipeline.errors import WorkIntakeRejectedError
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.api import API_TASK_BOARD_PIPELINE_FAILED

logger = get_logger(__name__)


async def process_task_board_pipeline(
    *,
    adapter: TaskBoardEntryAdapter,
    filing: TaskBoardFiling,
) -> None:
    """Drive a board filing through the work pipeline spine.

    Runs in a detached background task; the HTTP handler already
    returned ``202``. Failures are logged at WARNING (intake-rejection)
    or ERROR (any other pipeline failure) keyed by ``correlation_id``;
    ``MemoryError`` and ``RecursionError`` propagate. ``CancelledError``
    propagates so app shutdown does not convert a cancellation into a
    spurious error log.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    try:
        await adapter.submit(filing)
    except asyncio.CancelledError:
        raise
    except WorkIntakeRejectedError as exc:
        # Intake declining the work is a normal outcome, not a defect.
        logger.warning(
            API_TASK_BOARD_PIPELINE_FAILED,
            correlation_id=filing.correlation_id,
            project=filing.project,
            outcome="intake_rejected",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            API_TASK_BOARD_PIPELINE_FAILED,
            exc,
            correlation_id=filing.correlation_id,
            project=filing.project,
            outcome="pipeline_error",
        )


def spawn_task_board_pipeline(
    *,
    sim_state: ClientSimulationState,
    adapter: TaskBoardEntryAdapter,
    filing: TaskBoardFiling,
) -> None:
    """Spawn + track the background board-pipeline run.

    A detached task (not a ``TaskGroup``) is correct here: the create
    handler returns ``202`` immediately and the pipeline run outlives
    that scope by design. Lifecycle mirrors ``_spawn_intake_pipeline``
    in ``controllers/requests.py``: a strong reference in
    ``sim_state.background_tasks`` keeps the task from being GC'd
    mid-flight, the exception logger is attached before the
    set-discard so a fast-completing failure still surfaces, and the
    reference is added synchronously here (no ``await`` between
    ``create_task`` and ``add``).
    """
    task = asyncio.create_task(
        process_task_board_pipeline(adapter=adapter, filing=filing),
    )
    task.add_done_callback(
        log_task_exceptions(
            logger,
            API_TASK_BOARD_PIPELINE_FAILED,
            correlation_id=filing.correlation_id,
        ),
    )
    task.add_done_callback(sim_state.background_tasks.discard)
    sim_state.background_tasks.add(task)


__all__ = ["process_task_board_pipeline", "spawn_task_board_pipeline"]
