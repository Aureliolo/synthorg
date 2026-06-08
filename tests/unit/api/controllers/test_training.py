"""Tests for the training controller's id-keyed routing.

The routes are addressed by the agent's stable id (a UUID), not its
name; these tests assert the routes are mounted and resolve the agent by
id before reaching the controller body.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.types import stable_agent_id
from synthorg.hr.registry import AgentRegistryService
from tests._shared import LoopAsyncClient

_AGENT_NAME = "train-agent"


def _make_identity() -> AgentIdentity:
    return AgentIdentity(
        id=stable_agent_id(_AGENT_NAME),
        name=_AGENT_NAME,
        role="developer",
        department="eng",
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=datetime(2026, 3, 24, tzinfo=UTC).date(),
    )


@pytest.mark.unit
class TestTrainingControllerRouting:
    """Training routes are mounted under ``/agents/{agent_id}/training``."""

    async def test_create_plan_unknown_agent_404(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            f"/api/v1/agents/{stable_agent_id('ghost')}/training/plan",
            json={},
        )
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    async def test_get_latest_plan_unknown_agent_404(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            f"/api/v1/agents/{stable_agent_id('ghost')}/training/plan",
        )
        assert resp.status_code == 404

    async def test_create_plan_resolves_registered_agent(
        self,
        async_test_client: LoopAsyncClient,
        agent_registry: AgentRegistryService,
    ) -> None:
        """A registered agent's id resolves past the agent lookup.

        The endpoint then creates the plan (or 503s if the training plan
        service is not wired in this deployment); either way it is not a
        404, which proves the id route reaches the controller body.
        """
        await agent_registry.register(_make_identity())
        resp = await async_test_client.post(
            f"/api/v1/agents/{stable_agent_id(_AGENT_NAME)}/training/plan",
            json={},
        )
        assert resp.status_code != 404
