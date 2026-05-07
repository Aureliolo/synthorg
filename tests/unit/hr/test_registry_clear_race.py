"""Concurrency tests for HRRegistry.clear.

Production-safe ``clear`` must hold the same lock as ``register`` and
``unregister`` so a contending writer never observes a partial clear.
"""

import asyncio
import uuid
from collections.abc import Coroutine
from typing import Any

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.types import NotBlankStr
from synthorg.hr.registry import AgentRegistryService
from tests.unit.hr.conftest import make_agent_identity

pytestmark = pytest.mark.unit


def _make_identity(suffix: str) -> AgentIdentity:
    return make_agent_identity(
        agent_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"clear-race-{suffix}")),
        name=f"agent-{suffix}",
    )


async def _record_outcome(
    coro: Coroutine[Any, Any, Any],
    sink: list[BaseException | None],
) -> None:
    """Run *coro* under TaskGroup without aborting siblings on failure.

    Per CLAUDE.md async-concurrency rule: when running tasks in a
    TaskGroup where one task's failure must NOT cancel the others,
    wrap each body in a small helper that catches Exception and
    records a safe outcome (re-raising only ``MemoryError`` /
    ``RecursionError``). That keeps the per-task assertion surface
    intact under structured concurrency.
    """
    try:
        await coro
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        sink.append(exc)
    else:
        sink.append(None)


async def test_clear_concurrent_with_register_no_partial_state() -> None:
    """``clear`` racing with 50 ``register`` calls leaves no half-cleared state."""
    registry = AgentRegistryService()
    barrier = asyncio.Barrier(51)
    results: list[BaseException | None] = []

    async def register_one(suffix: str) -> None:
        await barrier.wait()
        await registry.register(_make_identity(suffix))

    async def clear_under_lock() -> None:
        await barrier.wait()
        await registry.clear()

    async with asyncio.TaskGroup() as tg:
        tg.create_task(_record_outcome(clear_under_lock(), results))
        for i in range(50):
            tg.create_task(_record_outcome(register_one(f"{i:03d}"), results))

    # Each ``register_one`` uses a distinct UUIDv5 (different suffix)
    # and the registry starts empty, so a duplicate-id collision is
    # impossible. Any task exception therefore signals a broken lock
    # contract -- not a tolerable concurrency outcome -- and must
    # fail the test rather than be masked.
    for outcome in results:
        assert outcome is None, (
            f"unexpected task exception: {type(outcome).__name__}: {outcome}"
        )

    # Final state: every agent that survived clear() is fully present.
    final_agents = await registry.list_active()
    for agent in final_agents:
        # Each surviving entry must round-trip through ``get`` -- if the
        # clear had landed mid-register the agent dict would have keys
        # without their values.
        fetched = await registry.get(NotBlankStr(str(agent.id)))
        assert fetched is not None
        assert fetched.id == agent.id
