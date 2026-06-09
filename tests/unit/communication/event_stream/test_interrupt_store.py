"""Tests for InterruptStore."""

import asyncio
from datetime import UTC, datetime

import pytest

from synthorg.communication.event_stream.interrupt import (
    Interrupt,
    InterruptResolution,
    InterruptStore,
    InterruptType,
    ResumeDecision,
)

_TS = datetime(2026, 4, 13, tzinfo=UTC)


def _make_interrupt(**overrides: object) -> Interrupt:
    defaults: dict[str, object] = {
        "id": "int-001",
        "type": InterruptType.TOOL_APPROVAL,
        "session_id": "session-abc",
        "agent_id": "agent-eng-001",
        "created_at": _TS,
        "timeout_seconds": 300.0,
        "tool_name": "deploy_service",
    }
    defaults.update(overrides)
    return Interrupt(**defaults)  # type: ignore[arg-type]


def _make_resolution(
    interrupt_id: str = "int-001",
    **overrides: object,
) -> InterruptResolution:
    defaults: dict[str, object] = {
        "interrupt_id": interrupt_id,
        "decision": ResumeDecision.APPROVE,
        "resolved_at": datetime(2026, 4, 13, 0, 5, tzinfo=UTC),
        "resolved_by": "admin-user",
    }
    defaults.update(overrides)
    return InterruptResolution(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
class TestInterruptStore:
    async def test_create_and_get(self) -> None:
        store = InterruptStore()
        interrupt = _make_interrupt()
        await store.create(interrupt)
        result = await store.get("int-001")
        assert result is not None
        assert result.id == "int-001"

    async def test_get_nonexistent_returns_none(self) -> None:
        store = InterruptStore()
        result = await store.get("nonexistent")
        assert result is None

    async def test_create_duplicate_raises(self) -> None:
        store = InterruptStore()
        await store.create(_make_interrupt())
        with pytest.raises(ValueError, match="already exists"):
            await store.create(_make_interrupt())

    async def test_list_pending_all(self) -> None:
        store = InterruptStore()
        await store.create(_make_interrupt(id="i1"))
        await store.create(_make_interrupt(id="i2"))
        pending = await store.list_pending()
        assert len(pending) == 2

    async def test_list_pending_by_session(self) -> None:
        store = InterruptStore()
        await store.create(_make_interrupt(id="i1", session_id="s1"))
        await store.create(_make_interrupt(id="i2", session_id="s2"))
        pending = await store.list_pending(session_id="s1")
        assert len(pending) == 1
        assert pending[0].id == "i1"

    async def test_resolve_returns_interrupt(self) -> None:
        store = InterruptStore()
        await store.create(_make_interrupt())
        resolution = _make_resolution()
        result = await store.resolve(resolution)
        assert result is not None
        assert result.id == "int-001"

    async def test_resolve_removes_from_pending(self) -> None:
        store = InterruptStore()
        await store.create(_make_interrupt())
        await store.resolve(_make_resolution())
        result = await store.get("int-001")
        assert result is None

    async def test_resolve_nonexistent_returns_none(self) -> None:
        store = InterruptStore()
        result = await store.resolve(_make_resolution(interrupt_id="nope"))
        assert result is None

    async def test_resolve_signals_waiter(self) -> None:
        store = InterruptStore()
        await store.create(_make_interrupt())

        async def _resolve_after_yield() -> None:
            # Yield control so the waiter can start, then resolve.
            await asyncio.sleep(0)
            await store.resolve(_make_resolution())

        task = asyncio.create_task(_resolve_after_yield())
        result = await store.wait_for_resolution("int-001", timeout=5.0)
        await task
        assert result is not None
        assert result.decision == ResumeDecision.APPROVE

    async def test_wait_for_resolution_timeout(self) -> None:
        store = InterruptStore()
        await store.create(_make_interrupt())
        # No resolution is provided, so wait_for_resolution always
        # times out deterministically (asyncio.wait_for on an unset
        # Event fires TimeoutError after the timeout elapses).
        result = await store.wait_for_resolution("int-001", timeout=0)
        assert result is None

    async def test_wait_for_nonexistent_returns_none(self) -> None:
        store = InterruptStore()
        result = await store.wait_for_resolution("nonexistent", timeout=0.01)
        assert result is None


@pytest.mark.unit
class TestInterruptStoreRaceConditions:
    """Race-condition regression tests.

    Concurrent ``create()`` / ``resolve()`` / ``wait_for_resolution()``
    must not corrupt the pending / events / results dicts.
    """

    async def test_concurrent_create_same_id_one_succeeds(self) -> None:
        store = InterruptStore()
        n_creators = 20
        barrier = asyncio.Barrier(n_creators)

        async def attempt_create() -> bool:
            await barrier.wait()
            try:
                await store.create(_make_interrupt())
            except ValueError:
                return False
            return True

        results = await asyncio.gather(
            *(attempt_create() for _ in range(n_creators)),
        )
        # Exactly one creator wins; the others see "already exists".
        assert sum(results) == 1

    async def test_resolve_during_wait_timeout_no_double_pop(self) -> None:
        store = InterruptStore()
        await store.create(_make_interrupt())

        async def slow_resolve() -> None:
            # Yield once so the waiter starts ``event.wait()`` first.
            await asyncio.sleep(0)
            await store.resolve(_make_resolution())

        async def waiter() -> InterruptResolution | None:
            return await store.wait_for_resolution("int-001", timeout=1.0)

        # Run resolver and waiter concurrently; the waiter should
        # observe the resolution and the dicts should end up empty.
        resolution, _ = await asyncio.gather(waiter(), slow_resolve())
        assert resolution is not None
        # Waiting again returns None (interrupt already cleaned up).
        again = await store.wait_for_resolution("int-001", timeout=0.01)
        assert again is None

    async def test_concurrent_resolve_only_one_succeeds(self) -> None:
        store = InterruptStore()
        await store.create(_make_interrupt())
        n_resolvers = 20
        barrier = asyncio.Barrier(n_resolvers)

        async def attempt_resolve() -> Interrupt | None:
            await barrier.wait()
            return await store.resolve(_make_resolution())

        results = await asyncio.gather(
            *(attempt_resolve() for _ in range(n_resolvers)),
        )
        # First resolver returns the interrupt; subsequent ones see it
        # gone (returns None and logs INTERRUPT_NOT_FOUND).
        non_none = [r for r in results if r is not None]
        assert len(non_none) == 1
