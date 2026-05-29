# mypy: disable-error-code="explicit-any"
"""Edge-case tests for :class:`PendingFuturesRegistry`."""

import asyncio

import pytest

from synthorg.communication.conflict_resolution.escalation.models import (
    WinnerDecision,
)
from synthorg.communication.conflict_resolution.escalation.registry import (
    PendingFuturesRegistry,
)

pytestmark = pytest.mark.unit


def _decision(agent_id: str = "agent-a") -> WinnerDecision:
    return WinnerDecision(winning_agent_id=agent_id, reasoning="test")


class TestRegistryBasics:
    async def test_register_then_resolve_wakes_future(self) -> None:
        reg = PendingFuturesRegistry()
        future = await reg.register("esc-1")
        decision = _decision()
        assert await reg.resolve("esc-1", decision) is True
        assert future.done()
        assert future.result() == decision

    async def test_register_twice_raises(self) -> None:
        reg = PendingFuturesRegistry()
        _ = await reg.register("esc-1")
        with pytest.raises(ValueError, match="already registered"):
            _ = await reg.register("esc-1")

    async def test_resolve_without_register_returns_false(self) -> None:
        reg = PendingFuturesRegistry()
        assert await reg.resolve("esc-missing", _decision()) is False

    async def test_cancel_cancels_future(self) -> None:
        reg = PendingFuturesRegistry()
        future = await reg.register("esc-cancel")
        assert await reg.cancel("esc-cancel") is True
        assert future.cancelled()

    async def test_cancel_without_register_returns_false(self) -> None:
        reg = PendingFuturesRegistry()
        assert await reg.cancel("esc-missing") is False

    async def test_is_registered_reflects_state(self) -> None:
        reg = PendingFuturesRegistry()
        assert await reg.is_registered("esc-1") is False
        _ = await reg.register("esc-1")
        assert await reg.is_registered("esc-1") is True
        await reg.resolve("esc-1", _decision())
        # Resolving pops the entry out of the map.
        assert await reg.is_registered("esc-1") is False

    async def test_close_cancels_all_pending_futures(self) -> None:
        reg = PendingFuturesRegistry()
        a = await reg.register("esc-a")
        b = await reg.register("esc-b")
        await reg.close()
        assert a.cancelled()
        assert b.cancelled()
        # Registry is reset so new registrations work afterwards.
        fresh = await reg.register("esc-a")
        await reg.resolve("esc-a", _decision())
        assert fresh.done()


class TestRegistryConcurrency:
    async def test_register_resolve_roundtrip_under_gather(self) -> None:
        reg = PendingFuturesRegistry()
        future = await reg.register("esc-gather")

        async def awaiter() -> WinnerDecision | object:
            return await future

        async def resolver() -> None:
            # Yield once so the awaiter is actually suspended on the Future.
            await asyncio.sleep(0)
            await reg.resolve("esc-gather", _decision())

        decision, _ = await asyncio.gather(awaiter(), resolver())
        assert isinstance(decision, WinnerDecision)
