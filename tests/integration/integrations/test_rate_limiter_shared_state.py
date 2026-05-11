"""Rate limiter shared state: cross-worker coordination.

Exercises two ``SharedRateLimitCoordinator`` instances sharing an
``InMemoryMessageBus`` so their sliding windows converge. Acquires
on coordinator A must be visible to coordinator B and count
against B's local limit.
"""

import asyncio

import pytest

from synthorg.communication.bus.memory import InMemoryMessageBus
from synthorg.communication.bus_protocol import MessageBus
from synthorg.integrations.errors import ConnectionRateLimitError
from synthorg.integrations.rate_limiting import shared_state as shared_state_module
from synthorg.integrations.rate_limiting.shared_state import (
    SharedRateLimitCoordinator,
    get_coordinator,
    set_coordinator_factory_sync,
)


@pytest.mark.integration
class TestSharedRateLimitCoordinator:
    async def test_acquire_rejects_over_budget_single_coordinator(
        self,
        memory_bus: InMemoryMessageBus,
    ) -> None:
        coord = SharedRateLimitCoordinator(
            bus=memory_bus,
            connection_name="test-single",
            max_rpm=2,
        )
        await coord.start()
        try:
            await coord.acquire()
            await coord.acquire()
            with pytest.raises(ConnectionRateLimitError):
                await coord.acquire()
        finally:
            await coord.stop()

    async def test_cross_worker_coordination(
        self,
        memory_bus: InMemoryMessageBus,
    ) -> None:
        """Two coordinators on the same bus share their windows."""
        coord_a = SharedRateLimitCoordinator(
            bus=memory_bus,
            connection_name="cross-worker",
            max_rpm=2,
        )
        coord_b = SharedRateLimitCoordinator(
            bus=memory_bus,
            connection_name="cross-worker",
            max_rpm=2,
        )
        # Deterministic synchronization: wrap coord_b's ``_ingest`` so
        # every time the poll loop accepts a remote acquire event we
        # ``set()`` an event the test can ``wait_for``. Avoids the
        # previous polling loop that would flake under CI load.
        ingest_event = asyncio.Event()
        original_ingest = coord_b._ingest

        async def _observing_ingest(message: object) -> None:
            await original_ingest(message)
            # Only flip the event once both coord_a acquires have
            # landed in coord_b's local view.
            if len(coord_b._window) >= 2:
                ingest_event.set()

        coord_b._ingest = _observing_ingest  # type: ignore[method-assign]

        await coord_a.start()
        await coord_b.start()
        try:
            # Coordinator A acquires both slots.
            await coord_a.acquire()
            await coord_a.acquire()

            # Wait for both publishes to propagate through the
            # in-memory bus into coord_b's ingest path.
            await asyncio.wait_for(ingest_event.wait(), timeout=2.0)
            assert len(coord_b._window) >= 2

            # Coordinator B should now reject because the global
            # sliding window is full.
            with pytest.raises(ConnectionRateLimitError):
                await coord_b.acquire()
        finally:
            await coord_a.stop()
            await coord_b.stop()


@pytest.mark.integration
class TestCoordinatorFactory:
    def test_factory_none_by_default(self) -> None:
        # Save & restore the module-global factory so the test does
        # not leak into later tests.
        original = shared_state_module._coordinator_factory
        shared_state_module._coordinator_factory = None
        try:
            assert get_coordinator("nonexistent") is None
        finally:
            shared_state_module._coordinator_factory = original

    def test_factory_creates_coordinator(self) -> None:
        from tests._shared import mock_of

        original_factory = shared_state_module._coordinator_factory
        original_coordinators = dict(shared_state_module._coordinators)
        try:
            shared_state_module._coordinators.clear()
            bus = mock_of[MessageBus]()
            set_coordinator_factory_sync(
                lambda name: SharedRateLimitCoordinator(
                    bus=bus,
                    connection_name=name,
                ),
            )
            coord = get_coordinator("test-factory")
            assert coord is not None
            assert coord._connection_name == "test-factory"
        finally:
            shared_state_module._coordinators.clear()
            shared_state_module._coordinators.update(original_coordinators)
            shared_state_module._coordinator_factory = original_factory
