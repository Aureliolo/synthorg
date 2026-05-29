"""Tests for BatchedTrigger."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from synthorg.engine.evolution.triggers.batched import BatchedTrigger


class TestBatchedTrigger:
    """BatchedTrigger fires based on time intervals."""

    @pytest.mark.unit
    async def test_fires_on_first_call(self) -> None:
        trigger = BatchedTrigger(interval_seconds=3600)
        ctx = MagicMock()
        assert await trigger.should_trigger(agent_id="agent-1", context=ctx)

    @pytest.mark.unit
    async def test_does_not_fire_within_interval(self) -> None:
        trigger = BatchedTrigger(interval_seconds=3600)
        ctx = MagicMock()
        assert await trigger.should_trigger(agent_id="agent-1", context=ctx)
        # Immediately again -- should not fire.
        assert not await trigger.should_trigger(agent_id="agent-1", context=ctx)

    @pytest.mark.unit
    async def test_fires_after_interval(self) -> None:
        trigger = BatchedTrigger(interval_seconds=60)
        ctx = MagicMock()

        # First call fires and records time.
        assert await trigger.should_trigger(agent_id="agent-1", context=ctx)

        # Backdate the last run.
        trigger._last_run["agent-1"] = datetime.now(UTC) - timedelta(seconds=120)
        assert await trigger.should_trigger(agent_id="agent-1", context=ctx)

    @pytest.mark.unit
    async def test_independent_per_agent(self) -> None:
        trigger = BatchedTrigger(interval_seconds=3600)
        ctx = MagicMock()
        assert await trigger.should_trigger(agent_id="agent-1", context=ctx)
        # Different agent -- should still fire.
        assert await trigger.should_trigger(agent_id="agent-2", context=ctx)
        # agent-1 again within interval -- should not.
        assert not await trigger.should_trigger(agent_id="agent-1", context=ctx)

    @pytest.mark.unit
    async def test_record_run(self) -> None:
        trigger = BatchedTrigger()
        await trigger.record_run("agent-1")
        assert "agent-1" in trigger._last_run

    @pytest.mark.unit
    async def test_concurrent_record_and_should_trigger_is_race_free(self) -> None:
        """Parallel record_run + should_trigger must not corrupt _last_run.

        The internal ``asyncio.Lock`` serialises writes; without it the
        interleaving would be undefined.  With it, every agent's entry
        must be a valid ``datetime`` after the storm.
        """
        trigger = BatchedTrigger(interval_seconds=3600)
        ctx = MagicMock()
        agents = [f"agent-{i}" for i in range(50)]

        async def _writer(agent_id: str) -> None:
            await trigger.record_run(agent_id)

        async def _reader(agent_id: str) -> None:
            await trigger.should_trigger(agent_id=agent_id, context=ctx)

        async with asyncio.TaskGroup() as tg:
            for agent_id in agents:
                _ = tg.create_task(_writer(agent_id))
                _ = tg.create_task(_reader(agent_id))
                _ = tg.create_task(_writer(agent_id))

        assert set(trigger._last_run) == set(agents)
        for ts in trigger._last_run.values():
            assert isinstance(ts, datetime)
            assert ts.tzinfo is UTC

    @pytest.mark.unit
    def test_name(self) -> None:
        assert BatchedTrigger().name == "batched"
