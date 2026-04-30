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
    """``wait()`` wakes immediately on cancel from a sibling thread.

    Synchronisation: the waiter runs in its own thread and signals
    ``waiter_started`` immediately before calling ``token.wait()``;
    the canceller blocks on ``waiter_started`` so ``cancel()`` only
    fires once the waiter is actively exercising the wait path.
    A pre-set cancel state would short-circuit ``wait()`` and pass
    even if the blocking-wait wake path were broken.
    """
    token = CancellationToken()
    waiter_started = threading.Event()
    waiter_result: list[bool] = []

    def _waiter() -> None:
        waiter_started.set()
        waiter_result.append(token.wait(timeout=5.0))

    def _canceller() -> None:
        waiter_started.wait()
        token.cancel()

    waiter = threading.Thread(target=_waiter)
    canceller = threading.Thread(target=_canceller)
    waiter.start()
    canceller.start()
    waiter.join()
    canceller.join()
    assert not waiter.is_alive()
    assert not canceller.is_alive()
    assert waiter_result == [True]


def test_wait_without_timeout_blocks_until_cancel() -> None:
    """``wait(None)`` blocks until cancel; a sibling thread cancels.

    Same waiter-started synchronisation as the timeout variant: the
    canceller fires only after the waiter is parked in ``wait()``.
    """
    token = CancellationToken()
    waiter_started = threading.Event()
    waiter_result: list[bool] = []

    def _waiter() -> None:
        waiter_started.set()
        waiter_result.append(token.wait())

    def _canceller() -> None:
        waiter_started.wait()
        token.cancel()

    waiter = threading.Thread(target=_waiter)
    canceller = threading.Thread(target=_canceller)
    waiter.start()
    canceller.start()
    waiter.join()
    canceller.join()
    assert not waiter.is_alive()
    assert not canceller.is_alive()
    assert waiter_result == [True]
