"""POSIX signal handlers for orderly shutdown.

This module installs explicit asyncio ``SIGTERM``/``SIGINT`` handlers
so we can log the signal the moment it arrives (before the ASGI
lifespan begins cancelling in-flight requests) and flag an
``AppState.shutdown_requested`` event that long-lived subsystems can
poll or ``await`` to exit early instead of waiting for cancellation.

Windows has no POSIX signals; the asyncio proactor event loop raises
``NotImplementedError`` on :meth:`add_signal_handler`. The helper logs
a DEBUG event and returns instead, so the app still boots.
"""

import asyncio
import signal
import sys
from collections.abc import Callable  # noqa: TC003
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_SHUTDOWN_HANDLER_SKIPPED,
    API_SHUTDOWN_SIGNAL_RECEIVED,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState

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
    if current_platform == "win32":
        logger.debug(
            API_SHUTDOWN_HANDLER_SKIPPED,
            reason="non-posix-platform",
            platform=current_platform,
        )
        return

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
