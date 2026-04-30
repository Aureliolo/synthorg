"""Cancellation token for fine-tuning pipeline stages.

Provides a cooperative cancellation mechanism: the orchestrator
sets the token, and each stage checks it between batches.
"""

import threading

from synthorg.memory.errors import FineTuneCancelledError
from synthorg.observability import get_logger
from synthorg.observability.events.memory import MEMORY_FINE_TUNE_CANCELLED

logger = get_logger(__name__)


class CancellationToken:
    """Cooperative cancellation signal for pipeline stages.

    Thread-safe via ``threading.Event``.  The orchestrator calls
    ``cancel()`` from the event loop; stage functions call ``check()``
    between batches, potentially from worker threads (via
    ``asyncio.to_thread``).
    """

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Signal cancellation."""
        self._event.set()
        logger.info(MEMORY_FINE_TUNE_CANCELLED, source="token")

    @property
    def is_cancelled(self) -> bool:
        """Whether cancellation has been requested."""
        return self._event.is_set()

    def check(self) -> None:
        """Raise ``FineTuneCancelledError`` if cancelled.

        Call this between batches in each pipeline stage.

        Raises:
            FineTuneCancelledError: If cancellation was requested.
        """
        if self._event.is_set():
            msg = "Fine-tuning pipeline run was cancelled"
            logger.warning(MEMORY_FINE_TUNE_CANCELLED, source="stage_check")
            raise FineTuneCancelledError(msg)

    def wait(self, timeout: float | None = None) -> bool:
        """Block until cancellation is signalled.

        Useful for stage workers that have nothing to do but wait for
        a cancel signal (e.g. between batches with no data ready):
        spinning on ``is_cancelled`` with ``time.sleep`` burns CPU and
        adds a polling delay before cancel is observed; this method
        wakes immediately when ``cancel()`` is called.

        Args:
            timeout: Maximum seconds to wait, or ``None`` to block
                indefinitely.

        Returns:
            ``True`` if cancellation was signalled, ``False`` if the
            wait timed out.
        """
        return self._event.wait(timeout)
