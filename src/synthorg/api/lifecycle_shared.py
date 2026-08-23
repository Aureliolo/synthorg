# module-kind: code
"""Shared lifecycle primitives: the safe-stop helper + startup-failure cleanup.

``_try_stop`` is the per-service safe-await used by both the startup-failure
cleanup and the ordered shutdown; ``_cleanup_on_failure`` tears down the
already-started services in reverse order when ``_safe_startup`` raises. Split
into a leaf module so :mod:`api.lifecycle_startup` and :mod:`api.lifecycle`
both import them without an import cycle.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterator
from contextvars import ContextVar
from typing import Final, NamedTuple, Protocol

from synthorg.api.bus_bridge import MessageBusBridge
from synthorg.backup.service import BackupService
from synthorg.communication.bus_protocol import MessageBus
from synthorg.core.lifecycle_constants import DEFAULT_DRAIN_TIMEOUT_SECONDS
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.dispatcher import SettingsChangeDispatcher

logger = get_logger(__name__)

# Per-service stop budget for the runtime background services cleaned up on a
# startup failure. The lifecycle-lock services (health probers, OAuth token
# manager, webhook event bridge) can drain in-flight work up to
# ``DEFAULT_DRAIN_TIMEOUT_SECONDS``; the quick poll-loop services (event
# stream hub, tunnel provider) finish well inside it. A single bounded budget
# keeps any one hung stop from blocking the rest of the reverse cleanup.
_CLEANUP_STOP_TIMEOUT_SECONDS: Final[float] = DEFAULT_DRAIN_TIMEOUT_SECONDS

# Seconds a step is still granted once the shared window is spent. Zero would
# be the arithmetically honest answer and the wrong one: the steps at the end
# of the teardown are the ones that persist state (the audit-chain flush, the
# persistence disconnect), so they must be ATTEMPTED even when the services
# ahead of them ran the window down. A floor buys them a real, if small, try.
_EXHAUSTED_STEP_FLOOR_SECONDS: Final[float] = 0.5

# Total the floor may add past the window, across every step that draws it.
# The floor is what lets the sequence overrun, and a PER-STEP floor overruns by
# however many steps happen to remain: the teardown has 33 of them, so half a
# second each is 16.5s, past the margin the grace period leaves. Drawing the
# floor from one reserve makes the overrun a property of this constant rather
# than of how many services the teardown happens to stop, which is the only
# form of it a deployment can size a grace period against.
_FLOOR_RESERVE_SECONDS: Final[float] = 10.0

# The smallest grant a step may receive, once even the reserve is spent. Zero
# is not usable here: ``asyncio.wait_for`` with a non-positive timeout raises
# without ever starting the coroutine, so a zero grant does not shorten the
# tail steps, it SKIPS them, which is the outcome the floor exists to prevent.
# A step that is merely slow gets nothing useful from this, but one that
# completes without blocking (a healthy persistence disconnect) still runs.
# It costs this much per remaining step, which is why it is this small.
_MIN_STEP_GRANT_SECONDS: Final[float] = 0.05


class _ShutdownWindow:
    """The remaining teardown budget, plus the reserve the floor draws on.

    Args:
        remaining: Returns the seconds left in the window, measured against the
            caller's own clock. Held as a callable rather than an instant
            because the clock is a seam this leaf module must not reach for:
            whoever opens the window owns it.
    """

    __slots__ = ("_remaining", "reserve_left")

    def __init__(self, remaining: Callable[[], float]) -> None:
        self._remaining = remaining
        self.reserve_left = _FLOOR_RESERVE_SECONDS

    def step_budget(self, timeout: float | None) -> float | None:
        """Clamp one step's budget to what the window and reserve allow.

        Args:
            timeout: The step's own budget, or ``None`` for unbounded.

        Returns:
            The seconds this step may take.
        """
        left = self._remaining()
        if left <= 0.0:
            # Window spent: draw the floor from the reserve, so the overrun
            # stays bounded by the reserve rather than by the step count.
            drawn = min(_EXHAUSTED_STEP_FLOOR_SECONDS, self.reserve_left)
            self.reserve_left -= drawn
            # Never zero, or the tail is skipped rather than shortened.
            left = max(drawn, _MIN_STEP_GRANT_SECONDS)
        return left if timeout is None else min(timeout, left)


# A context variable rather than a parameter threaded through every step,
# because the population is DERIVED rather than listed. The window has to bind
# every ``_try_stop`` in the teardown, and a step added later that nobody
# remembered to thread would silently escape it, which is the failure this
# exists to prevent. Ambient by construction cannot be forgotten.
_active_window: ContextVar[_ShutdownWindow | None] = ContextVar(
    "synthorg_shutdown_window", default=None
)


@contextlib.contextmanager
def shutdown_window(remaining: Callable[[], float]) -> Iterator[None]:
    """Bound every :func:`_try_stop` in this scope to one shared budget.

    Per-service budgets are each individually sane and sum to far more than any
    container's termination grace period, so on a slow stop SIGKILL arrives
    mid-sequence and the steps that lose are the ones at the end. Clamping each
    step to what is left of one window keeps the sequence reaching its own
    final steps instead of being killed part-way through.

    Args:
        remaining: Returns the seconds left in the window, measured against the
            caller's own clock.

    Yields:
        ``None``; the window is in force for the duration of the block.
    """
    token = _active_window.set(_ShutdownWindow(remaining))
    try:
        yield
    finally:
        _active_window.reset(token)


def _windowed_timeout(timeout: float | None) -> float | None:
    """Clamp a per-service budget to what is left of the shared window.

    Args:
        timeout: The step's own budget, or ``None`` for unbounded.

    Returns:
        The seconds this step may take: its own budget outside a window, and
        inside one the smaller of that and what the window and its floor
        reserve still allow.
    """
    window = _active_window.get()
    if window is None:
        return timeout
    return window.step_budget(timeout)


# Structural seam over the optional synthorg[distributed] JetStreamTaskQueue;
# consumers: _cleanup_on_failure, _safe_shutdown.
class _AsyncStartStop(Protocol):
    """Minimal async lifecycle Protocol used by the distributed task queue hook.

    The concrete type is ``synthorg.workers.claim.JetStreamTaskQueue``, but
    importing that here would force the optional ``synthorg[distributed]`` extra
    to be installed even for deployments that never use the queue. A structural
    Protocol with ``start()``/``stop()`` gives the lifecycle helpers a real
    shape without the hard dependency.
    """

    async def start(self) -> None:
        """Open the connection / initialise resources."""
        ...

    async def stop(self) -> None:
        """Tear down the connection / release resources."""
        ...


class _Stoppable(Protocol):
    """Structural seam for any service the reverse cleanup stops via ``stop()``."""

    async def stop(self) -> None:
        """Drain in-flight work and release resources."""
        ...


class _StopStep(NamedTuple):
    """One reverse-cleanup stop step in ``_cleanup_on_failure``.

    ``started`` / ``service`` gate the step (both must be truthy);
    ``timeout`` / ``service_name`` flow straight into :func:`_try_stop`;
    ``log_phase`` emits the stopping/stopped lifecycle lines the
    distributed task queue records around its stop.
    """

    started: bool
    service: _Stoppable | None
    error_msg: str
    service_name: str | None = None
    timeout: float | None = None
    log_phase: bool = False


async def _try_stop(
    coro: Awaitable[None],
    event: str,
    error_msg: str,
    *,
    timeout: float | None = None,  # noqa: ASYNC109 -- per-service shutdown budget
    service: str | None = None,
) -> bool:
    """Await *coro* inside a safe try/except, logging failures.

    ``MemoryError`` and ``RecursionError`` are re-raised immediately; all other
    exceptions are logged and swallowed so that sibling shutdown steps can still
    run.

    When *timeout* is set, a ``TimeoutError`` is logged at ERROR with the
    ``service`` label (when provided) and shutdown continues with the next
    service. Services that hang past their per-service budget must not block the
    whole shutdown window.

    Args:
        coro: The stop/disconnect coroutine to await.
        event: Log event name for the failure branch.
        error_msg: Human-readable context for the failure log.
        timeout: Optional per-service budget (seconds), clamped to what is
            left of any :func:`shutdown_window` in force.
        service: Optional service label for the structured log.

    Returns:
        ``True`` when *coro* completes without raising, ``False`` when an
        exception was swallowed. Callers use this to guard "stopped" log lines
        so they only fire on actual success.

    Raises:
        MemoryError: Re-raised unchanged (never swallowed).
        RecursionError: Re-raised unchanged (never swallowed).
    """
    budget = _windowed_timeout(timeout)
    awaitable = asyncio.wait_for(coro, timeout=budget) if budget is not None else coro
    try:
        await awaitable
    except MemoryError, RecursionError:
        raise
    except TimeoutError as exc:
        from synthorg.observability.events.api import (  # noqa: PLC0415
            API_APP_SHUTDOWN_TIMEOUT,
        )

        # logger.error rather than logger.exception so TimeoutError frame-locals
        # never serialize the awaited coroutine's state (which may include
        # secret-bearing shutdown objects).
        log_exception_redacted(
            logger,
            API_APP_SHUTDOWN_TIMEOUT,
            exc,
            service=service,
            timeout_seconds=timeout,
            context=error_msg,
        )
        return False
    except Exception as exc:  # noqa: BLE001 -- shutdown best-effort: log and continue
        logger.warning(
            event,
            service=service,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            context=error_msg,
        )
        return False
    return True


async def _cleanup_on_failure(  # noqa: PLR0913
    *,
    persistence: PersistenceBackend | None,
    started_persistence: bool,
    message_bus: MessageBus | None,
    started_bus: bool,
    bridge: MessageBusBridge | None = None,
    started_bridge: bool = False,
    settings_dispatcher: SettingsChangeDispatcher | None = None,
    started_settings_dispatcher: bool = False,
    task_engine: TaskEngine | None = None,
    started_task_engine: bool = False,
    distributed_task_queue: _AsyncStartStop | None = None,
    started_distributed_task_queue: bool = False,
    distributed_backend_services: _AsyncStartStop | None = None,
    started_distributed_backend_services: bool = False,
    backup_service: BackupService | None = None,
    started_backup_service: bool = False,
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None = None,
    started_approval_timeout_scheduler: bool = False,
    event_stream_hub: _AsyncStartStop | None = None,
    started_event_stream_hub: bool = False,
    oauth_token_manager: _AsyncStartStop | None = None,
    started_oauth_token_manager: bool = False,
    integration_health_prober: _AsyncStartStop | None = None,
    started_integration_health_prober: bool = False,
    provider_health_prober: _AsyncStartStop | None = None,
    started_provider_health_prober: bool = False,
    chat_inbound_consumer: _AsyncStartStop | None = None,
    started_chat_inbound_consumer: bool = False,
) -> None:
    """Reverse cleanup on startup failure.

    The runtime background services (event stream hub, integration services,
    health probers) start AFTER the core services, so they are stopped FIRST
    here -- in reverse of their ``_run_startup`` start order. Each stop is
    bounded by ``_CLEANUP_STOP_TIMEOUT_SECONDS`` so a hung drain cannot block
    the rest of the reverse cleanup. Every runtime-service param defaults to
    ``None`` / ``False`` so the core-only ``_safe_startup`` failure path
    (which never started them) passes nothing and the blocks no-op.
    """
    runtime_budget = _CLEANUP_STOP_TIMEOUT_SECONDS
    # Reverse of the ``_run_startup`` start order: runtime background services
    # first (each on the bounded runtime budget), then the core services, then
    # the message bus. Persistence (a ``disconnect()``, not a ``stop()``) is
    # the final explicit step below.
    steps = (
        _StopStep(
            started_event_stream_hub,
            event_stream_hub,
            "Cleanup: failed to stop event stream hub",
            "event_stream_hub",
            runtime_budget,
        ),
        _StopStep(
            started_oauth_token_manager,
            oauth_token_manager,
            "Cleanup: failed to stop OAuth token manager",
            "oauth_token_manager",
            runtime_budget,
        ),
        _StopStep(
            started_integration_health_prober,
            integration_health_prober,
            "Cleanup: failed to stop integration health prober",
            "integration_health_prober",
            runtime_budget,
        ),
        _StopStep(
            started_provider_health_prober,
            provider_health_prober,
            "Cleanup: failed to stop provider health prober",
            "provider_health_prober",
            runtime_budget,
        ),
        # The Socket-Mode consumer holds a live outbound WebSocket, so a
        # startup failure after it starts would otherwise leave it
        # consuming (and resuming approvals) for the process lifetime:
        # shutdown cleanup never runs on a failed startup.
        _StopStep(
            started_chat_inbound_consumer,
            chat_inbound_consumer,
            "Cleanup: failed to stop chat inbound consumer",
            "chat_inbound_consumer",
            runtime_budget,
        ),
        _StopStep(
            started_approval_timeout_scheduler,
            approval_timeout_scheduler,
            "Cleanup: failed to stop approval timeout scheduler",
        ),
        _StopStep(
            started_backup_service,
            backup_service,
            "Cleanup: failed to stop backup service",
        ),
        _StopStep(
            started_task_engine, task_engine, "Cleanup: failed to stop task engine"
        ),
        _StopStep(
            started_settings_dispatcher,
            settings_dispatcher,
            "Cleanup: failed to stop settings dispatcher",
        ),
        _StopStep(started_bridge, bridge, "Cleanup: failed to stop message bus bridge"),
        _StopStep(
            started_distributed_backend_services,
            distributed_backend_services,
            "Cleanup: failed to stop distributed backend services",
        ),
        _StopStep(
            started_distributed_task_queue,
            distributed_task_queue,
            "Cleanup: failed to stop distributed task queue",
            "distributed_task_queue",
            log_phase=True,
        ),
        _StopStep(started_bus, message_bus, "Cleanup: failed to stop message bus"),
    )
    for step in steps:
        if not (step.started and step.service is not None):
            continue
        if step.log_phase:
            logger.info(
                API_APP_STARTUP, service=step.service_name, phase="stopping_on_cleanup"
            )
        ok = await _try_stop(
            step.service.stop(),
            API_APP_STARTUP,
            step.error_msg,
            timeout=step.timeout,
            service=step.service_name,
        )
        if step.log_phase and ok:
            logger.info(
                API_APP_STARTUP, service=step.service_name, phase="stopped_on_cleanup"
            )
    if started_persistence and persistence is not None:
        await _try_stop(
            persistence.disconnect(),
            API_APP_STARTUP,
            "Cleanup: failed to disconnect persistence",
        )
