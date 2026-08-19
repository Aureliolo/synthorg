"""POSIX signal handlers for orderly shutdown.

This module installs explicit asyncio ``SIGTERM``/``SIGINT`` handlers
so we can log the signal the moment it arrives (before the ASGI
lifespan begins cancelling in-flight requests) and flag an
``AppState.shutdown_requested`` event that long-lived subsystems can
poll or ``await`` to exit early instead of waiting for cancellation.

Windows's proactor event loop raises ``NotImplementedError`` on
:meth:`add_signal_handler`, so on win32 the helper falls back to the C
``signal.signal`` API (main thread only) and re-enters the loop via
``call_soon_threadsafe``; a worker-thread lifespan logs a DEBUG event
and returns, so the app still boots.

Shutdown window (operator reference): on ``SIGTERM`` the handler here
sets ``AppState.shutdown_requested`` and closes the cooperative drain
gate (new parallel agent tasks are rejected immediately), then hands the
signal on to uvicorn, which triggers the ASGI lifespan shutdown and runs
the ordered on-shutdown teardown (``api/lifecycle_runner_shutdown``:
drain in-flight work, stop background services, then ``_safe_shutdown``
-> persistence disconnect). uvicorn's ``timeout_graceful_shutdown`` (75s,
configured in ``api/server.py``) bounds that teardown; if it overruns,
uvicorn escalates to ``SIGKILL``. Every per-service stop step is
therefore individually bounded so the aggregate stays inside that 75s
window.

**Handing the signal on is not optional, and is why the chain exists.**
``loop.add_signal_handler`` REPLACES whatever is registered for a signal,
and uvicorn registers its own ``handle_exit`` before it runs the app,
whose lifespan startup is what installs these. Ours therefore lands
second and uvicorn's is gone. Registering without a chain meant uvicorn
never learned the signal had arrived: a live ``docker stop -t 60``
logged ``api.shutdown.signal.received``, went on running the subsystem
reconciler twenty seconds later, and exited 137 at the grace deadline
with no teardown at all, stranding every in-flight task at
``in_progress``. So the entry point registers the server's own exit via
:func:`set_shutdown_chain`, and **absent a chain these handlers are not
installed**, leaving uvicorn's intact rather than silently disarming it.
"""

import asyncio
import signal
import sys
import threading
from collections.abc import Callable
from types import FrameType

from synthorg.api.state import AppState
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_SHUTDOWN_HANDLER_SKIPPED,
    API_SHUTDOWN_SIGNAL_RECEIVED,
)

logger = get_logger(__name__)


_POSIX_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGTERM, signal.SIGINT)

#: What to hand the signal on to once the early flag is set: the running
#: server's own exit. Process-global because there is exactly one server per
#: process, and the entry point that owns it cannot reach the lifespan startup
#: that installs the handlers.
_shutdown_chain: Callable[[signal.Signals], None] | None = None


def set_shutdown_chain(chain: Callable[[signal.Signals], None] | None) -> None:
    """Register what the signal handlers hand the signal on to.

    Called by the entry point with the running server's ``handle_exit``
    before the app starts, so the handlers installed during lifespan
    startup have somewhere to pass the signal. Passing ``None`` clears it,
    which suppresses handler installation entirely rather than leaving
    uvicorn's handler replaced by one that only logs.

    Args:
        chain: Receives the signal after the early flag is set, or ``None``
            to leave signal handling wholly to whoever already owns it.
    """
    global _shutdown_chain  # noqa: PLW0603 -- one server per process
    _shutdown_chain = chain


def install_shutdown_handlers(app_state: AppState) -> None:
    """Register POSIX ``SIGTERM``/``SIGINT`` handlers on the running loop.

    Idempotent: the shared-app test fixture reuses a single ``AppState``
    across lifespan re-enters. Repeated calls overwrite the handler
    with a fresh closure that captures the same ``app_state`` and
    ``.clear()`` the ``shutdown_requested`` event so a second lifespan
    does not observe a stale "already set" state from the previous
    run.

    On non-POSIX (Windows dev), logs DEBUG and returns.
    """
    # Reset the shutdown flag so a reused AppState starts clean even
    # if the prior lifespan observed SIGTERM.  Safe before any handler
    # is registered and a no-op when already clear.
    app_state.shutdown_requested.clear()

    # No chain means somebody else's handler is the only thing that will
    # ever stop this process (uvicorn's supervisor topologies, the test
    # portal). Installing ours would replace theirs with one that logs and
    # returns, which is how the process came to ignore SIGTERM entirely.
    if _shutdown_chain is None:
        logger.debug(
            API_SHUTDOWN_HANDLER_SKIPPED,
            reason="no-shutdown-chain",
        )
        return

    # ``sys.platform`` narrows to a literal on the current host, so
    # mypy would flag the POSIX branch as unreachable on a Windows
    # development machine (and vice versa).  Read it through a local
    # variable so the runtime check survives type checking on either
    # platform.
    current_platform: str = sys.platform

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (called from sync context); uvicorn owns
        # signals in that case.  Log so operators see the skip.
        logger.debug(
            API_SHUTDOWN_HANDLER_SKIPPED,
            reason="no-running-loop",
        )
        return

    if current_platform == "win32":
        # The proactor loop has no ``add_signal_handler``; fall back to
        # the C ``signal.signal`` API, which delivers SIGTERM/SIGINT on
        # the Windows main thread. The callback runs between bytecodes
        # off-loop, so it re-enters the loop via ``call_soon_threadsafe``
        # to flag shutdown on the loop's thread.
        _install_win32_handlers(loop, app_state)
        return

    skipped: list[str] = []
    for sig in _POSIX_SIGNALS:
        try:
            loop.add_signal_handler(
                sig,
                _make_handler(sig, app_state),
            )
        except NotImplementedError, ValueError, RuntimeError:
            # Proactor event loops (embedded runtimes, subinterpreters)
            # raise ``NotImplementedError``.  Non-main-thread execution
            # (e.g. Litestar's ``TestClient`` portal runs the lifespan on
            # a worker thread and ``loop.add_signal_handler`` bottoms out
            # in ``signal.set_wakeup_fd`` which raises ``ValueError:
            # set_wakeup_fd only works in main thread of the main
            # interpreter``) is equally benign -- uvicorn in production
            # owns the signal handler when this branch fires.
            # ``RuntimeError`` covers the "loop is closed" race and any
            # other loop-state refusal.  Collect the skipped signal
            # names and log once at the end so a mixed outcome
            # (e.g. SIGTERM registered but SIGINT refused) is visible
            # instead of silently exiting after the first skip.
            skipped.append(sig.name)

    if skipped:
        logger.debug(
            API_SHUTDOWN_HANDLER_SKIPPED,
            reason="loop-lacks-signal-handler",
            signals=tuple(skipped),
        )


def _make_handler(
    sig: signal.Signals,
    app_state: AppState,
) -> Callable[[], None]:
    """Bind ``sig`` + ``app_state`` into a zero-arg handler closure.

    Returns:
        ``Callable[[], None]`` instance.
    """

    def handler() -> None:
        _on_signal(sig, app_state)

    return handler


def _install_win32_handlers(
    loop: asyncio.AbstractEventLoop,
    app_state: AppState,
) -> None:
    """Install ``signal.signal`` SIGTERM/SIGINT handlers on win32.

    ``signal.signal`` only works on the interpreter's main thread, so a
    lifespan driven from a worker thread (Litestar's ``TestClient``
    portal) logs DEBUG and returns rather than raising. uvicorn owns the
    signal in that case.
    """
    if threading.current_thread() is not threading.main_thread():
        logger.debug(
            API_SHUTDOWN_HANDLER_SKIPPED,
            reason="non-main-thread-win32",
        )
        return
    skipped: list[str] = []
    for sig in _POSIX_SIGNALS:
        try:
            signal.signal(sig, _make_win32_handler(sig, app_state, loop))
        except ValueError, OSError:
            # ``ValueError`` when not on the main thread (belt-and-braces
            # with the guard above), ``OSError`` for a signal the host
            # cannot deliver. uvicorn's own handler covers production.
            skipped.append(sig.name)
        except RuntimeError as exc:
            # ``signal.signal`` does not document ``RuntimeError``; an
            # unexpected one is more likely a real defect than a benignly
            # undeliverable signal, so surface it at WARNING rather than
            # silently demoting it to the DEBUG "refused" bucket.
            logger.warning(
                API_SHUTDOWN_HANDLER_SKIPPED,
                reason="win32-signal-unexpected-error",
                signal=sig.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            skipped.append(sig.name)
    if skipped:
        logger.debug(
            API_SHUTDOWN_HANDLER_SKIPPED,
            reason="win32-signal-refused",
            signals=tuple(skipped),
        )


def _make_win32_handler(
    sig: signal.Signals,
    app_state: AppState,
    loop: asyncio.AbstractEventLoop,
) -> Callable[[int, FrameType | None], None]:
    """Bind a 2-arg C-signal handler that re-enters the loop thread.

    Returns:
        A ``signal.signal``-compatible ``(signum, frame)`` callback.
    """

    def handler(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        try:
            loop.call_soon_threadsafe(_on_signal, sig, app_state)
        except RuntimeError:
            # The loop was already closed (teardown raced the signal):
            # the ASGI lifespan shutdown is already underway, so the
            # early-stop nudge is moot. Swallow rather than let the
            # C-signal callback propagate an exception.
            logger.debug(
                API_SHUTDOWN_HANDLER_SKIPPED,
                reason="loop-closed",
                signals=(sig.name,),
            )

    return handler


def _on_signal(sig: signal.Signals, app_state: AppState) -> None:
    """Flag the app for shutdown, then hand the signal on to the server.

    Does NOT call ``loop.stop()``: stopping is the server's job, reached
    through the chain. Our own job is to make the signal observable to
    subsystems that want to stop early, which is the whole reason for
    taking the signal ahead of uvicorn rather than leaving it alone.
    """
    logger.info(
        API_SHUTDOWN_SIGNAL_RECEIVED,
        signal=sig.name,
    )
    event = app_state.shutdown_requested
    if not event.is_set():
        event.set()
    # Close the cooperative drain gate immediately so the multi-agent
    # coordinator rejects new parallel agent tasks the moment the signal
    # arrives; the bounded grace-then-cancel of in-flight tasks runs
    # later from the on-shutdown hook via ``initiate_shutdown``.
    app_state.shutdown_manager.request_shutdown()
    # Hand it on. Without this the process observes the signal and keeps
    # running until the orchestrator's grace period expires and SIGKILL
    # lands mid-work, which is the one outcome the whole bounded-teardown
    # design exists to avoid.
    chain = _shutdown_chain
    if chain is not None:
        chain(sig)
