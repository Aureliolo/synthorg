"""Direct tests for ``CancellationToken``."""

import threading

import pytest

from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.errors import FineTuneCancelledError

pytestmark = pytest.mark.unit


def test_initial_state_not_cancelled() -> None:
    token = CancellationToken()
    assert token.is_cancelled is False


def test_cancel_flips_is_cancelled() -> None:
    token = CancellationToken()
    token.cancel()
    assert token.is_cancelled is True


def test_check_raises_after_cancel() -> None:
    token = CancellationToken()
    token.cancel()
    with pytest.raises(FineTuneCancelledError):
        token.check()


def test_check_does_not_raise_before_cancel() -> None:
    token = CancellationToken()
    # Should be a no-op before cancel.
    token.check()


def test_wait_returns_true_when_already_cancelled() -> None:
    """``wait()`` returns immediately if cancellation already fired."""
    token = CancellationToken()
    token.cancel()
    assert token.wait(timeout=0.01) is True


def test_wait_returns_false_on_timeout() -> None:
    """``wait(timeout)`` returns ``False`` when the timeout elapses."""
    token = CancellationToken()
    # No cancel; the wait should time out and report False.
    assert token.wait(timeout=0.01) is False


def test_wait_unblocks_when_cancel_fires_from_another_thread() -> None:
    """``wait()`` wakes immediately on cancel from a sibling thread."""
    token = CancellationToken()
    cancelled = threading.Event()

    def _canceller() -> None:
        # Brief delay so the main thread is parked in ``wait()``.
        cancelled.wait(timeout=1.0)
        token.cancel()

    cancelled.set()
    canceller = threading.Thread(target=_canceller)
    canceller.start()
    try:
        # Without cancellation this would block 5 s; with the
        # threading.Event under the hood it returns immediately.
        result = token.wait(timeout=5.0)
        assert result is True
    finally:
        canceller.join(timeout=2.0)


def test_wait_without_timeout_blocks_until_cancel() -> None:
    """``wait(None)`` blocks until cancel; a sibling thread cancels."""
    token = CancellationToken()

    def _canceller() -> None:
        token.cancel()

    canceller = threading.Thread(target=_canceller)
    canceller.start()
    try:
        # Pass timeout=None implicitly via the default; the helper
        # contract is that it returns True on cancel.
        assert token.wait() is True
    finally:
        canceller.join(timeout=2.0)
