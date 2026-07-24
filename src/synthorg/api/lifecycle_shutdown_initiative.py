# module-kind: code
"""Shutdown drains for the initiative loop's detached tails.

The rollup schedules three kinds of detached work (the SHIP retrospective, an
auto-replan, an integration dispatch), each on its own tracked registry. All
three must finish, or be bounded, before the stores they write to are
disconnected: a retrospective stranded mid-write loses the learning, and an
abandoned replan or integration dispatch leaves exactly the partial graph their
compensated ordering exists to prevent.

Extracted from the shutdown runner so that runner stays a readable teardown
order rather than a list of per-tail drain blocks.
"""

from collections.abc import Coroutine
from typing import Final

from synthorg.api.lifecycle import _try_stop
from synthorg.api.state import AppState
from synthorg.core.lifecycle_constants import DEFAULT_DRAIN_TIMEOUT_SECONDS
from synthorg.observability.events.api import API_APP_SHUTDOWN

#: Outer backstop past each drain's own internal deadline, so the inner
#: mechanism (which logs its pending count) always fires first.
_GRACE_SECONDS: Final[float] = 2.0


async def drain_initiative_tails(app_state: AppState) -> None:
    """Drain every detached initiative tail, bounded and best-effort.

    A no-op when the rollup service is unwired. Each drain is independently
    bounded, so one hanging tail cannot consume the whole shutdown window.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

    rollup = app_state.slice(EngineStateSlice).project_rollup_service
    if rollup is None:
        return
    tails: tuple[tuple[Coroutine[object, object, None], str, str], ...] = (
        (
            rollup.drain_retro_capture(timeout_sec=DEFAULT_DRAIN_TIMEOUT_SECONDS),
            "Failed to drain in-flight retrospective capture tasks",
            "ship_retro_capture_drain",
        ),
        (
            rollup.drain_replan_trigger(timeout_sec=DEFAULT_DRAIN_TIMEOUT_SECONDS),
            "Failed to drain in-flight initiative replans",
            "initiative_replan_drain",
        ),
        (
            rollup.drain_integration(timeout_sec=DEFAULT_DRAIN_TIMEOUT_SECONDS),
            "Failed to drain in-flight integration dispatches",
            "initiative_integration_drain",
        ),
        (
            rollup.drain_evaluation(timeout_sec=DEFAULT_DRAIN_TIMEOUT_SECONDS),
            "Failed to drain in-flight initiative evaluations",
            "initiative_evaluation_drain",
        ),
    )
    for coro, message, service in tails:
        await _try_stop(
            coro,
            API_APP_SHUTDOWN,
            message,
            timeout=DEFAULT_DRAIN_TIMEOUT_SECONDS + _GRACE_SECONDS,
            service=service,
        )
