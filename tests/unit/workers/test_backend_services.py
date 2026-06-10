# module-kind: tests
"""Lifecycle tests for the backend distributed-path service bundle.

Pins the start-rollback contract: an ordinary start failure stops the
already-started components (reverse order) before propagating, while
``MemoryError`` / ``RecursionError`` propagate immediately WITHOUT
running the rollback (which does async teardown work that may allocate
under catastrophic interpreter state).
"""

from collections.abc import Iterator
from typing import cast

import pytest
from typeguard import suppress_type_checks

from synthorg.workers.backend_services import DistributedBackendServices
from synthorg.workers.dead_letter import DeadLetterConsumer
from synthorg.workers.heartbeat_subscriber import WorkerHeartbeatSubscriber
from synthorg.workers.seen_claims_pruner import SeenClaimsPruner

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _suppress_typeguard_for_fakes() -> Iterator[None]:
    """Suppress typeguard: the bundle is driven with duck-typed components.

    These tests verify start/stop ordering and the critical-error
    carve-out, not component type conformance.
    """
    with suppress_type_checks():
        yield


class _FakeComponent:
    """Duck-typed lifecycle component recording start/stop calls."""

    def __init__(
        self,
        name: str,
        log: list[str],
        *,
        start_exc: BaseException | None = None,
    ) -> None:
        self._name = name
        self._log = log
        self._start_exc = start_exc
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._start_exc is not None:
            raise self._start_exc
        self._running = True
        self._log.append(f"start:{self._name}")

    async def stop(self) -> None:
        self._running = False
        self._log.append(f"stop:{self._name}")


def _bundle(
    log: list[str],
    *,
    dead_letter_start_exc: BaseException | None = None,
) -> DistributedBackendServices:
    return DistributedBackendServices(
        dead_letter=cast(
            "DeadLetterConsumer",
            _FakeComponent("dead_letter", log, start_exc=dead_letter_start_exc),
        ),
        pruner=cast("SeenClaimsPruner", _FakeComponent("pruner", log)),
        heartbeat=cast("WorkerHeartbeatSubscriber", _FakeComponent("heartbeat", log)),
    )


async def test_start_and_stop_run_in_documented_order() -> None:
    log: list[str] = []
    bundle = _bundle(log)

    await bundle.start()
    running_after_start = bool(bundle.is_running)
    assert log == ["start:pruner", "start:dead_letter", "start:heartbeat"]

    await bundle.stop()
    running_after_stop = bool(bundle.is_running)
    assert (running_after_start, running_after_stop) == (True, False)
    assert log[3:] == ["stop:heartbeat", "stop:dead_letter", "stop:pruner"]


async def test_ordinary_start_failure_rolls_back_started_components() -> None:
    log: list[str] = []
    msg = "nats unavailable"
    bundle = _bundle(log, dead_letter_start_exc=RuntimeError(msg))

    with pytest.raises(RuntimeError, match=msg):
        await bundle.start()

    # Pruner started first and must be stopped by the rollback.
    assert log == ["start:pruner", "stop:pruner"]


async def test_memory_error_during_start_skips_rollback() -> None:
    log: list[str] = []
    bundle = _bundle(log, dead_letter_start_exc=MemoryError())

    with pytest.raises(MemoryError):
        await bundle.start()

    # The carve-out must propagate BEFORE the rollback loop runs.
    assert log == ["start:pruner"]
