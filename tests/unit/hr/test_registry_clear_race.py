"""Concurrency tests for HRRegistry.clear (#1599 §4.1).

Production-safe ``clear`` must hold the same lock as ``register`` and
``unregister`` so a contending writer never observes a partial clear.
"""

import asyncio
import uuid

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


async def test_clear_concurrent_with_register_no_partial_state() -> None:
    """``clear`` racing with 50 ``register`` calls leaves no half-cleared state."""
    registry = AgentRegistryService()
    barrier = asyncio.Barrier(51)

    async def register_one(suffix: str) -> None:
        await barrier.wait()
        await registry.register(_make_identity(suffix))

    async def clear_under_lock() -> None:
        await barrier.wait()
        await registry.clear()

    register_tasks = [register_one(f"{i:03d}") for i in range(50)]
    await asyncio.gather(
        clear_under_lock(),
        *register_tasks,
        return_exceptions=True,  # AgentAlreadyRegisteredError noise tolerable
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
