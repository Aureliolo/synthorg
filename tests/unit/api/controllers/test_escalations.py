"""Unit tests for the human escalation approval queue."""

from datetime import UTC, datetime

import pytest

from synthorg.communication.conflict_resolution.escalation.models import (
    Escalation,
    EscalationStatus,
)
from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictPosition,
)
from synthorg.communication.enums import ConflictType
from synthorg.communication.state import CommunicationStateSlice
from synthorg.hr.seniority import SeniorityLevel
from tests._shared import LoopAsyncClient, as_pk, as_uuid, sid
from tests.unit.api.conftest import make_auth_headers

pytestmark = pytest.mark.unit

_BASE = "/api/v1/conflicts/escalations"
_WRITE_HEADERS = make_auth_headers("ceo")
_READ_HEADERS = make_auth_headers("observer")


def _make_escalation(
    *,
    escalation_id: str = "escalation-test-0001",
    conflict_id: str | None = None,
) -> Escalation:
    """Build a valid escalation for store-seeded tests.

    ``conflict_id`` defaults to a value derived from ``escalation_id`` so
    callers that create several escalations in the same test do not trip
    the queue's "one pending row per conflict" invariant by accident.
    """
    resolved_conflict_id = conflict_id or f"conflict-for-{escalation_id}"
    conflict = Conflict(
        id=as_uuid(resolved_conflict_id),
        type=ConflictType.ARCHITECTURE,
        subject="Choose backend framework",
        positions=(
            ConflictPosition(
                agent_id="agent-a",
                agent_department="engineering",
                agent_level=SeniorityLevel.SENIOR,
                position="use Framework A",
                reasoning="Framework A is more mature",
                timestamp=datetime.now(UTC),
            ),
            ConflictPosition(
                agent_id="agent-b",
                agent_department="engineering",
                agent_level=SeniorityLevel.SENIOR,
                position="use Framework B",
                reasoning="Framework B has better performance",
                timestamp=datetime.now(UTC),
            ),
        ),
        detected_at=datetime.now(UTC),
    )
    return Escalation(
        id=as_pk(escalation_id),
        conflict=conflict,
        created_at=datetime.now(UTC),
    )


class TestEscalationsController:
    async def test_list_empty(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get(_BASE, headers=_READ_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []

    async def test_list_returns_pending_rows(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        comms = async_test_client.app.state.app_state.slice(CommunicationStateSlice)
        store = comms.escalation_store
        assert store is not None
        await store.create(_make_escalation(escalation_id="escalation-list-01"))
        resp = await async_test_client.get(_BASE, headers=_READ_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["escalation"]["id"] == sid("escalation-list-01")

    async def test_get_missing_returns_404(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            f"{_BASE}/escalation-missing", headers=_READ_HEADERS
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["error_detail"]["error_category"] == "not_found"

    async def test_winner_decision_transitions_row_and_resolves_future(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        app_state = async_test_client.app.state.app_state
        store = app_state.slice(CommunicationStateSlice).escalation_store
        registry = app_state.slice(CommunicationStateSlice).escalation_registry
        assert store is not None
        assert registry is not None

        escalation = _make_escalation(escalation_id="escalation-winner-01")
        await store.create(escalation)
        future = await registry.register(str(escalation.id))

        resp = await async_test_client.post(
            f"{_BASE}/{escalation.id}/decision",
            json={
                "decision": {
                    "type": "winner",
                    "winning_agent_id": "agent-a",
                    "reasoning": "Decided by operator",
                },
            },
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["escalation"]["status"] == "decided"
        assert future.done()
        resolved = future.result()
        assert resolved.type == "winner"
        assert resolved.winning_agent_id == "agent-a"

    async def test_reject_decision_rejected_when_winner_only_strategy(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Default decision_strategy='winner' -> reject decisions yield 422."""
        app_state = async_test_client.app.state.app_state
        store = app_state.slice(CommunicationStateSlice).escalation_store
        assert store is not None
        escalation = _make_escalation(escalation_id="escalation-reject-01")
        await store.create(escalation)

        resp = await async_test_client.post(
            f"{_BASE}/{escalation.id}/decision",
            json={
                "decision": {
                    "type": "reject",
                    "reasoning": "No winner",
                },
            },
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["error_detail"]["error_category"] == "validation"
        row = await store.get(str(escalation.id))
        assert row is not None
        assert row.status == EscalationStatus.PENDING

    async def test_decision_on_already_decided_row_returns_409(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        app_state = async_test_client.app.state.app_state
        store = app_state.slice(CommunicationStateSlice).escalation_store
        registry = app_state.slice(CommunicationStateSlice).escalation_registry
        assert store is not None
        assert registry is not None
        escalation = _make_escalation(escalation_id="escalation-double-01")
        await store.create(escalation)
        await registry.register(str(escalation.id))

        body = {
            "decision": {
                "type": "winner",
                "winning_agent_id": "agent-a",
                "reasoning": "first",
            },
        }
        first = await async_test_client.post(
            f"{_BASE}/{escalation.id}/decision",
            json=body,
            headers=_WRITE_HEADERS,
        )
        assert first.status_code == 201

        second = await async_test_client.post(
            f"{_BASE}/{escalation.id}/decision",
            json=body,
            headers=_WRITE_HEADERS,
        )
        assert second.status_code == 409
        assert second.json()["error_detail"]["error_category"] == "conflict"

    async def test_cancel_transitions_to_cancelled(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        app_state = async_test_client.app.state.app_state
        store = app_state.slice(CommunicationStateSlice).escalation_store
        registry = app_state.slice(CommunicationStateSlice).escalation_registry
        assert store is not None
        assert registry is not None
        escalation = _make_escalation(escalation_id="escalation-cancel-01")
        await store.create(escalation)
        future = await registry.register(str(escalation.id))

        resp = await async_test_client.post(
            f"{_BASE}/{escalation.id}/cancel",
            json={"reason": "duplicate report"},
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["escalation"]["status"] == "cancelled"
        assert future.cancelled()

    async def test_decision_rate_limit_returns_429(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """After 30 decision attempts, the 31st is 429 + RFC 9457."""
        app_state = async_test_client.app.state.app_state
        store = app_state.slice(CommunicationStateSlice).escalation_store
        registry = app_state.slice(CommunicationStateSlice).escalation_registry
        assert store is not None
        assert registry is not None

        for i in range(31):
            escalation = _make_escalation(escalation_id=f"escalation-rl-{i:02d}")
            await store.create(escalation)
            await registry.register(str(escalation.id))

        saw_429 = False
        for i in range(31):
            resp = await async_test_client.post(
                f"{_BASE}/{sid(f'escalation-rl-{i:02d}')}/decision",
                json={
                    "decision": {
                        "type": "winner",
                        "winning_agent_id": "agent-a",
                        "reasoning": "ok",
                    },
                },
                headers=_WRITE_HEADERS,
            )
            if resp.status_code == 429:
                saw_429 = True
                assert resp.json()["error_detail"]["error_category"] == "rate_limit"
                assert resp.headers.get("Retry-After") is not None
                break
        assert saw_429, "expected a 429 within 31 decisions"
