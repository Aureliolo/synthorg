"""Error-handling branches for the shared rate-limit coordinator.

Bus failures (subscribe / unsubscribe / publish / receive) must
degrade the coordinator gracefully -- falling back to local-only
coordination or surfacing the failure -- rather than letting a
broad ``except`` swallow an interpreter-critical exception. These
exercise the ``reraise_critical`` guard sites with non-critical
exceptions so the recovery path runs.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from synthorg.communication.bus_protocol import MessageBus
from synthorg.integrations.rate_limiting import shared_state as shared_state_module
from synthorg.integrations.rate_limiting.shared_state import (
    SharedRateLimitCoordinator,
    set_coordinator_factory,
)
from tests._shared import mock_of


@pytest.mark.unit
class TestSharedRateLimitErrorPaths:
    async def test_start_subscribe_failure_falls_back_to_local(self) -> None:
        bus = mock_of[MessageBus](
            subscribe=AsyncMock(
                spec=MessageBus.subscribe,
                side_effect=RuntimeError("subscribe boom"),
            ),
        )
        coord = SharedRateLimitCoordinator(bus=bus, connection_name="sub-fail")

        await coord.start()

        # Subscribe failure is swallowed: coordinator marks itself
        # started but degraded to in-process (non-distributed) mode,
        # with no live bus subscription recorded.
        assert coord._started is True
        assert coord._distributed is False
        assert coord._task is None
        assert coord._subscribed is False

    async def test_stop_unsubscribe_failure_reraises_and_preserves_flags(
        self,
    ) -> None:
        bus = mock_of[MessageBus](
            unsubscribe=AsyncMock(
                spec=MessageBus.unsubscribe,
                side_effect=RuntimeError("unsubscribe boom"),
            ),
        )
        coord = SharedRateLimitCoordinator(bus=bus, connection_name="unsub-fail")
        coord._started = True
        coord._distributed = True
        coord._subscribed = True

        with pytest.raises(RuntimeError, match="unsubscribe boom"):
            await coord.stop()

        # A failed unsubscribe leaves the flags untouched so a later
        # start() cannot reuse the subscriber id against a ghost sub.
        assert coord._started is True
        assert coord._distributed is True
        assert coord._subscribed is True

    async def test_stop_after_local_only_fallback_skips_unsubscribe(self) -> None:
        unsubscribe = AsyncMock(spec=MessageBus.unsubscribe)
        bus = mock_of[MessageBus](
            subscribe=AsyncMock(
                spec=MessageBus.subscribe,
                side_effect=RuntimeError("subscribe boom"),
            ),
            unsubscribe=unsubscribe,
        )
        coord = SharedRateLimitCoordinator(bus=bus, connection_name="local-only")

        await coord.start()
        # Fell back to local-only: no bus registration was created.
        assert coord._subscribed is False

        # stop() must not try to unsubscribe a never-registered
        # subscriber (the bus would raise NotSubscribedError); it
        # cleanly resets the lifecycle flags instead.
        await coord.stop()

        unsubscribe.assert_not_awaited()
        assert coord._started is False
        assert coord._distributed is False

    async def test_publish_failure_falls_back_to_local(self) -> None:
        bus = mock_of[MessageBus](
            publish=AsyncMock(
                spec=MessageBus.publish,
                side_effect=RuntimeError("publish boom"),
            ),
        )
        coord = SharedRateLimitCoordinator(bus=bus, connection_name="pub-fail")
        coord._distributed = True

        await coord._publish_acquire(0.0)

        # Publish failure drops the coordinator to local-only mode.
        assert coord._distributed is False

    async def test_poll_loop_receive_failure_falls_back_and_exits(self) -> None:
        bus = mock_of[MessageBus](
            receive=AsyncMock(
                spec=MessageBus.receive,
                side_effect=RuntimeError("receive boom"),
            ),
        )
        coord = SharedRateLimitCoordinator(bus=bus, connection_name="poll-fail")
        coord._distributed = True

        # A non-cancellation error in the poll loop degrades to
        # local-only and returns (does not spin).
        await asyncio.wait_for(coord._poll_loop(), timeout=2.0)

        assert coord._distributed is False

    async def test_factory_swap_tolerates_stop_failure(self) -> None:
        original_factory = shared_state_module._coordinator_factory
        original_coordinators = dict(shared_state_module._coordinators)
        try:
            shared_state_module._coordinators.clear()
            bus = mock_of[MessageBus](
                unsubscribe=AsyncMock(
                    spec=MessageBus.unsubscribe,
                    side_effect=RuntimeError("stop boom"),
                ),
            )
            doomed = SharedRateLimitCoordinator(bus=bus, connection_name="doomed")
            doomed._started = True
            doomed._distributed = True
            doomed._subscribed = True
            shared_state_module._coordinators["doomed"] = doomed

            def new_factory(name: str) -> SharedRateLimitCoordinator:
                return SharedRateLimitCoordinator(
                    bus=mock_of[MessageBus](),
                    connection_name=name,
                )

            # The swap stops outgoing coordinators; a stop() failure is
            # logged, not propagated, so the swap still completes.
            await set_coordinator_factory(new_factory)

            assert shared_state_module._coordinator_factory is new_factory
            assert shared_state_module._coordinators == {}
        finally:
            shared_state_module._coordinators.clear()
            shared_state_module._coordinators.update(original_coordinators)
            shared_state_module._coordinator_factory = original_factory
