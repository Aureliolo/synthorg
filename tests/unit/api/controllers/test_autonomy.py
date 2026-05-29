"""Tests for the autonomy controller.

The controller is wired to the boot ``AutonomyChangeStrategy``
(default ``HUMAN_ONLY``): a change request enforces the D6 seniority
rule, consults the strategy, and enqueues a real approval item rather
than returning a hardcoded pending stub.
"""

from datetime import date
from uuid import UUID, uuid4

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import SeniorityLevel
from synthorg.hr.registry import AgentRegistryService
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

_BASE = "/api/v1/agents"
_WRITE_HEADERS = make_auth_headers("ceo")
_READ_HEADERS = make_auth_headers("observer")


def _url(agent_id: str = "agent-001") -> str:
    return f"{_BASE}/{agent_id}/autonomy"


def _make_identity(
    *,
    agent_id: UUID,
    level: SeniorityLevel = SeniorityLevel.MID,
) -> AgentIdentity:
    return AgentIdentity(
        id=agent_id,
        name=f"agent-{agent_id.hex[:8]}",
        role="developer",
        department="eng",
        level=level,
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )


@pytest.mark.unit
class TestGetAutonomy:
    async def test_get_autonomy(self, async_test_client: LoopAsyncClient) -> None:
        # Default autonomy flipped SEMI -> SUPERVISED (2026-04-23, #1538).
        resp = await async_test_client.get(_url("agent-42"), headers=_READ_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["agent_id"] == "agent-42"
        assert data["level"] == "supervised"
        assert data["promotion_pending"] is False

    async def test_get_autonomy_requires_read_access(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            _url(), headers={"Authorization": "Bearer invalid-token"}
        )
        assert resp.status_code == 401


@pytest.mark.unit
class TestUpdateAutonomy:
    async def test_pending_for_registered_agent(
        self,
        async_test_client: LoopAsyncClient,
        agent_registry: AgentRegistryService,
        approval_store: ApprovalStore,
    ) -> None:
        agent_id = uuid4()
        await agent_registry.register(
            _make_identity(agent_id=agent_id, level=SeniorityLevel.SENIOR),
        )

        resp = await async_test_client.post(
            _url(str(agent_id)),
            json={"level": "semi", "reason": "earned trust over Q1"},
            headers=_WRITE_HEADERS,
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["agent_id"] == str(agent_id)
        # HUMAN_ONLY: every change pends for human approval.
        assert data["promotion_pending"] is True

        # Prove the controller reached the real approval pipeline (the
        # old stubbed path returned promotion_pending without enqueuing
        # anything). A single PENDING autonomy:promote item for this
        # agent must now exist, attributed to the authenticated caller
        # rather than the "system" fallback.
        items = await approval_store.list_items()
        promote = [i for i in items if i.action_type == "autonomy:promote"]
        assert len(promote) == 1
        assert promote[0].metadata["agent_id"] == str(agent_id)
        assert promote[0].requested_by != "system"

    async def test_seniority_violation_forbidden(
        self,
        async_test_client: LoopAsyncClient,
        agent_registry: AgentRegistryService,
    ) -> None:
        agent_id = uuid4()
        await agent_registry.register(
            _make_identity(agent_id=agent_id, level=SeniorityLevel.JUNIOR),
        )

        resp = await async_test_client.post(
            _url(str(agent_id)),
            json={"level": "full", "reason": "wants full autonomy"},
            headers=_WRITE_HEADERS,
        )

        assert resp.status_code == 403

    async def test_unknown_agent_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            _url(str(uuid4())),
            json={"level": "semi", "reason": "no such agent"},
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 404

    async def test_missing_reason_rejected(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            _url("agent-42"),
            json={"level": "full"},
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code in (400, 422)

    async def test_update_autonomy_requires_write_access(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            _url(),
            json={"level": "full", "reason": "needs write access"},
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 403


@pytest.mark.unit
class TestAutonomyPathParamValidation:
    async def test_oversized_agent_id_rejected(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        long_id = "x" * 129
        resp = await async_test_client.get(
            _url(long_id),
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 400
