# module-kind: code
"""Shutdown drains for the initiative loop's detached tails.

The rollup schedules four kinds of detached work (an integration dispatch, a
judgement, an auto-replan, and the SHIP retrospective), each on its own tracked
registry. All four must finish, or be bounded, before the stores they write to
are disconnected: a retrospective stranded mid-write loses the learning, an
abandoned replan or integration dispatch leaves exactly the partial graph their
compensated ordering exists to prevent, and a judgement abandoned mid-flight
loses the only verdict that can complete the initiative.

**Order is producer-before-consumer, and the sequence runs twice.** A
judgement that lands during its own drain schedules a replan, and a replan
supersedes a plan whose task cancellations feed the retrospective's edge, so
draining in the other order would let the last stage enqueue work onto a
registry already declared empty. The second pass catches exactly that
hand-off; a third would only be needed if a replan scheduled a judgement, which
nothing does.

Extracted from the shutdown runner so that runner stays a readable teardown
order rather than a list of per-tail drain blocks.
"""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Final

from synthorg.api.lifecycle import _try_stop
from synthorg.api.state import AppState
from synthorg.core.lifecycle_constants import (
    DEFAULT_DRAIN_TIMEOUT_SECONDS,
    INITIATIVE_TAIL_TOTAL_DRAIN_BUDGET_SECONDS,
)
from synthorg.engine.initiative.rollup import ProjectRollupService
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_SHUTDOWN

logger = get_logger(__name__)

#: Outer backstop past each drain's own internal deadline, so the inner
#: mechanism (which logs its pending count) always fires first.
_GRACE_SECONDS: Final[float] = 2.0

#: Passes over the drain sequence. One pass cannot be enough: the stages hand
#: work to each other, and the second pass is what collects a hand-off made
#: during the first.
_DRAIN_PASSES: Final[int] = 2


def _drain_plan(
    rollup: ProjectRollupService,
) -> tuple[tuple[Callable[[], Coroutine[object, object, None]], str, str], ...]:
    """Return the drains in producer-before-consumer order.

    Each drain is a factory rather than a coroutine so it is created at the
    moment it is awaited: building them all up front would leave the remaining
    ones un-awaited (and their tails silently undrained) if an earlier one
    escaped.

    Returns:
        One ``(factory, failure message, service label)`` per tail.
    """
    timeout = DEFAULT_DRAIN_TIMEOUT_SECONDS
    return (
        (
            lambda: rollup.drain_integration(timeout_sec=timeout),
            "Failed to drain in-flight integration dispatches",
            "initiative_integration_drain",
        ),
        (
            lambda: rollup.drain_evaluation(timeout_sec=timeout),
            "Failed to drain in-flight initiative evaluations",
            "initiative_evaluation_drain",
        ),
        (
            lambda: rollup.drain_replan_trigger(timeout_sec=timeout),
            "Failed to drain in-flight initiative replans",
            "initiative_replan_drain",
        ),
        (
            lambda: rollup.drain_retro_capture(timeout_sec=timeout),
            "Failed to drain in-flight retrospective capture tasks",
            "ship_retro_capture_drain",
        ),
    )


async def drain_initiative_tails(app_state: AppState) -> None:
    """Drain every detached initiative tail, bounded and best-effort.

    A no-op when the rollup service is unwired. Each drain is independently
    bounded, so one hanging tail cannot consume the whole shutdown window.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

    rollup = app_state.slice(EngineStateSlice).project_rollup_service
    if rollup is None:
        return
    # The tails drain in series over two passes; an overall deadline caps the
    # whole sequence so a run of slow tails cannot cumulatively overrun the
    # server's graceful-shutdown window, even though each drain is also bounded
    # on its own. Abandoning here is the intended best-effort shutdown outcome.
    try:
        async with asyncio.timeout(INITIATIVE_TAIL_TOTAL_DRAIN_BUDGET_SECONDS):
            for _pass in range(_DRAIN_PASSES):
                for factory, message, service in _drain_plan(rollup):
                    await _try_stop(
                        factory(),
                        API_APP_SHUTDOWN,
                        message,
                        timeout=DEFAULT_DRAIN_TIMEOUT_SECONDS + _GRACE_SECONDS,
                        service=service,
                    )
    except TimeoutError:
        logger.warning(
            API_APP_SHUTDOWN,
            service="initiative_tail_drain",
            note=(
                "overall initiative-tail drain budget exhausted; abandoning "
                "remaining drains to stay within the graceful-shutdown window"
            ),
        )
