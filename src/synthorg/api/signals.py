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
    """Flag the app for shutdown and log the signal.

    Does NOT call ``loop.stop()`` -- uvicorn's own handler triggers the
    ASGI lifespan shutdown, which runs our ``on_shutdown`` hooks in
    order. Our job here is to make the signal observable to subsystems
    that want to stop early.
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
