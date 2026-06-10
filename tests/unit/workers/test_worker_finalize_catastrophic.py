"""Catastrophic-error carve-out for :meth:`Worker._finalize_claim`.

``_finalize_claim`` catches broad ``Exception`` from the ack/nack call,
emits a redacted ERROR log, and re-raises so the outer loop exits and
the pool restarts the worker. ``MemoryError`` / ``RecursionError`` must
escape that handler entirely so they propagate without log-handler work
(which may itself allocate or recurse) running first.
"""

from typing import TYPE_CHECKING, cast

import pytest

from synthorg.communication.config import NatsConfig
from synthorg.workers.claim import JetStreamTaskQueue, TaskClaim, TaskClaimStatus
from synthorg.workers.config import QueueConfig
from synthorg.workers.worker import Worker

if TYPE_CHECKING:
    from nats.aio.msg import Msg


async def _noop_executor(claim: TaskClaim) -> TaskClaimStatus:
    """Type-correct executor stub; ``_finalize_claim`` never invokes it."""
    del claim
    return TaskClaimStatus.SUCCESS


def _make_worker() -> Worker:
    queue = JetStreamTaskQueue(
        queue_config=QueueConfig(enabled=True),
        nats_config=NatsConfig(),
    )
    return Worker(
        queue_config=QueueConfig(enabled=True),
        task_queue=queue,
        executor=_noop_executor,
        worker_id="w-test",
    )


class _RaisingRaw:
    """Raw JetStream message stub whose ``ack`` raises the requested exception."""

    def __init__(self, *, exc: type[BaseException]) -> None:
        self._exc = exc

    async def ack(self) -> None:
        raise self._exc

    async def nak(self, delay: float = 0.0) -> None:
        del delay
        raise self._exc


@pytest.mark.unit
class TestFinalizeClaimCatastrophicErrors:
    """``_finalize_claim`` propagates ``MemoryError`` / ``RecursionError``."""

    @pytest.mark.parametrize("exc_cls", [MemoryError, RecursionError])
    async def test_ack_catastrophic_propagates(
        self,
        exc_cls: type[BaseException],
    ) -> None:
        """Terminal status routes through ``ack``; catastrophic errors bubble up."""
        worker = _make_worker()
        with pytest.raises(exc_cls):
            await worker._finalize_claim(
                cast("Msg", _RaisingRaw(exc=exc_cls)),
                TaskClaimStatus.SUCCESS,
            )

    @pytest.mark.parametrize("exc_cls", [MemoryError, RecursionError])
    async def test_nack_catastrophic_propagates(
        self,
        exc_cls: type[BaseException],
    ) -> None:
        """Non-terminal status routes through ``nack``; same carve-out applies."""
        worker = _make_worker()
        with pytest.raises(exc_cls):
            await worker._finalize_claim(
                cast("Msg", _RaisingRaw(exc=exc_cls)),
                TaskClaimStatus.RETRY,
            )

    async def test_ack_broad_exception_logged_and_reraised(self) -> None:
        """Pin the contract the carve-out is layered on: an ordinary
        ``Exception`` is logged via ``log_exception_redacted`` AND re-raised
        so the outer ``run()`` loop exits."""
        worker = _make_worker()

        class _RuntimeRaisingRaw:
            async def ack(self) -> None:
                msg = "transient ack failure"
                raise RuntimeError(msg)

            async def nak(self, delay: float = 0.0) -> None:
                del delay

        with pytest.raises(RuntimeError, match="transient ack failure"):
            await worker._finalize_claim(
                cast("Msg", _RuntimeRaisingRaw()),
                TaskClaimStatus.SUCCESS,
            )
