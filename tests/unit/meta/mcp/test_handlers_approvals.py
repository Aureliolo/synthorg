"""Unit tests for the approvals MCP handlers.

Exercises every branch a real service-shim handler ever takes: read
(list with filters + pagination), get with
not-found, write (create), write (approve with conflict), destructive
write (reject with all guardrail branches).
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import structlog.testing

from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.agent import AgentIdentity
from synthorg.core.approval import ApprovalItem
from synthorg.meta.mcp.handlers.approvals import APPROVAL_HANDLERS
from synthorg.observability.events.mcp import (
    MCP_ADMIN_OP_EXECUTED,
    MCP_HANDLER_GUARDRAIL_VIOLATED,
    MCP_HANDLER_INVOKE_FAILED,
)
from tests._shared import as_uuid, make_app_state, sid
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


# --- fixtures ---------------------------------------------------------------


def _make_item(
    *,
    approval_id: str | None = None,
    status: ApprovalStatus = ApprovalStatus.PENDING,
    risk: ApprovalRiskLevel = ApprovalRiskLevel.MEDIUM,
    action_type: str = "deploy",
) -> ApprovalItem:
    """Build a minimal valid ``ApprovalItem`` for tests."""
    return ApprovalItem(
        id=as_uuid(approval_id) if approval_id else uuid4(),
        action_type=action_type,
        title="Test approval",
        description="Test description",
        requested_by="test-agent",
        risk_level=risk,
        status=status,
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def fake_approval_store() -> AsyncMock:
    """AsyncMock conforming to ``ApprovalStoreProtocol``."""
    store = AsyncMock()
    store.clear = lambda: None
    return store


@pytest.fixture
def fake_app_state(fake_approval_store: AsyncMock) -> AppState:
    """Minimal app_state exposing only ``approval_store``."""
    return make_app_state(approval_store=fake_approval_store)


@pytest.fixture
def actor() -> AgentIdentity:
    """Minimal AgentIdentity stand-in for handler tests."""
    return make_test_actor(name="chief-of-staff")


# --- list ------------------------------------------------------------------


class TestApprovalsList:
    async def test_list_happy_path(
        self,
        fake_app_state: AppState,
        fake_approval_store: AsyncMock,
    ) -> None:
        fake_approval_store.list_items.return_value = (
            _make_item(approval_id="a1"),
            _make_item(approval_id="a2"),
        )
        handler = APPROVAL_HANDLERS["synthorg_approvals_list"]

        result = await handler(
            app_state=fake_app_state,
            arguments={},
            actor=None,
        )
        body = json.loads(result)

        assert body["status"] == "ok"
        assert len(body["data"]) == 2
        assert {d["id"] for d in body["data"]} == {sid("a1"), sid("a2")}

    async def test_list_passes_filters_to_store(
        self,
        fake_app_state: AppState,
        fake_approval_store: AsyncMock,
    ) -> None:
        fake_approval_store.list_items.return_value = ()
        handler = APPROVAL_HANDLERS["synthorg_approvals_list"]

        await handler(
            app_state=fake_app_state,
            arguments={
                "status": "pending",
                "risk_level": "high",
                "action_type": "deploy",
            },
            actor=None,
        )

        fake_approval_store.list_items.assert_awaited_once()
        kwargs = fake_approval_store.list_items.call_args.kwargs
        assert kwargs["status"] == ApprovalStatus.PENDING
        assert kwargs["risk_level"] == ApprovalRiskLevel.HIGH
        assert kwargs["action_type"] == "deploy"

    async def test_list_paginates(
        self,
        fake_app_state: AppState,
        fake_approval_store: AsyncMock,
    ) -> None:
        fake_approval_store.list_items.return_value = tuple(
            _make_item(approval_id=f"a{i}") for i in range(25)
        )
        handler = APPROVAL_HANDLERS["synthorg_approvals_list"]

        result = await handler(
            app_state=fake_app_state,
            arguments={"offset": 10, "limit": 5},
            actor=None,
        )
        body = json.loads(result)

        assert len(body["data"]) == 5
        assert [d["id"] for d in body["data"]] == [sid(f"a{i}") for i in range(10, 15)]
        assert body["pagination"] == {"total": 25, "offset": 10, "limit": 5}

    async def test_list_rejects_invalid_status_filter(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = APPROVAL_HANDLERS["synthorg_approvals_list"]
        result = await handler(
            app_state=fake_app_state,
            arguments={"status": "bogus"},
            actor=None,
        )
        body = json.loads(result)
        assert body["status"] == "error"
        assert body["domain_code"] == "invalid_argument"


# --- get -------------------------------------------------------------------


class TestApprovalsGet:
    async def test_get_happy_path(
        self,
        fake_app_state: AppState,
        fake_approval_store: AsyncMock,
    ) -> None:
        fake_approval_store.get.return_value = _make_item(approval_id="a1")
        handler = APPROVAL_HANDLERS["synthorg_approvals_get"]

        result = await handler(
            app_state=fake_app_state,
            arguments={"approval_id": "a1"},
            actor=None,
        )
        body = json.loads(result)

        assert body["status"] == "ok"
        assert body["data"]["id"] == sid("a1")
        fake_approval_store.get.assert_awaited_once_with("a1")

    async def test_get_not_found(
        self,
        fake_app_state: AppState,
        fake_approval_store: AsyncMock,
    ) -> None:
        fake_approval_store.get.return_value = None
        handler = APPROVAL_HANDLERS["synthorg_approvals_get"]

        result = await handler(
            app_state=fake_app_state,
            arguments={"approval_id": "missing"},
            actor=None,
        )
        body = json.loads(result)

        assert body["status"] == "error"
        assert body["domain_code"] == "not_found"

    async def test_get_missing_approval_id(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = APPROVAL_HANDLERS["synthorg_approvals_get"]
        result = await handler(
            app_state=fake_app_state,
            arguments={},
            actor=None,
        )
        body = json.loads(result)
        assert body["status"] == "error"
        assert body["domain_code"] == "invalid_argument"


# --- create ----------------------------------------------------------------


class TestApprovalsCreate:
    async def test_create_happy_path(
        self,
        fake_app_state: AppState,
        fake_approval_store: AsyncMock,
        actor: AgentIdentity,
    ) -> None:
        fake_approval_store.add.return_value = None
        handler = APPROVAL_HANDLERS["synthorg_approvals_create"]

        result = await handler(
            app_state=fake_app_state,
            arguments={
                "action_type": "deploy",
                "title": "Deploy v2",
                "description": "Ship the v2 bundle",
                "risk_level": "medium",
            },
            actor=actor,
        )
        body = json.loads(result)

        assert body["status"] == "ok"
        assert body["data"]["action_type"] == "deploy"
        # actor.id (UUID string) is preferred over actor.name for a
        # stable audit identifier; we just verify the field is populated
        # with the fixture actor's UUID rather than a display name.
        assert body["data"]["requested_by"] == str(actor.id)
        fake_approval_store.add.assert_awaited_once()

    async def test_create_requires_actor(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = APPROVAL_HANDLERS["synthorg_approvals_create"]
        result = await handler(
            app_state=fake_app_state,
            arguments={
                "action_type": "deploy",
                "title": "X",
                "description": "Y",
                "risk_level": "low",
            },
            actor=None,
        )
        body = json.loads(result)
        # Create attributes to the calling actor; anonymous calls fail.
        assert body["status"] == "error"
        assert body["domain_code"] in {"invalid_argument", "guardrail_violated"}

    async def test_create_rejects_invalid_risk_level(
        self,
        fake_app_state: AppState,
        actor: AgentIdentity,
    ) -> None:
        handler = APPROVAL_HANDLERS["synthorg_approvals_create"]
        result = await handler(
            app_state=fake_app_state,
            arguments={
                "action_type": "deploy",
                "title": "X",
                "description": "Y",
                "risk_level": "SPICY",
            },
            actor=actor,
        )
        body = json.loads(result)
        assert body["status"] == "error"
        assert body["domain_code"] == "invalid_argument"


# --- approve ---------------------------------------------------------------


class TestApprovalsApprove:
    async def test_approve_happy_path(
        self,
        fake_app_state: AppState,
        fake_approval_store: AsyncMock,
        actor: AgentIdentity,
    ) -> None:
        item = _make_item(approval_id="a1")
        fake_approval_store.get.return_value = item
        fake_approval_store.save_if_pending.side_effect = (
            lambda updated: updated  # echo
        )
        handler = APPROVAL_HANDLERS["synthorg_approvals_approve"]

        result = await handler(
            app_state=fake_app_state,
            arguments={"approval_id": "a1", "comment": "LGTM"},
            actor=actor,
        )
        body = json.loads(result)

        assert body["status"] == "ok"
        assert body["data"]["status"] == "approved"
        assert body["data"]["decided_by"] == str(actor.id)
        assert body["data"]["decision_reason"] == "LGTM"

    async def test_approve_not_found(
        self,
        fake_app_state: AppState,
        fake_approval_store: AsyncMock,
        actor: AgentIdentity,
    ) -> None:
        fake_approval_store.get.return_value = None
        handler = APPROVAL_HANDLERS["synthorg_approvals_approve"]

        result = await handler(
            app_state=fake_app_state,
            arguments={"approval_id": "missing"},
            actor=actor,
        )
        body = json.loads(result)
        assert body["status"] == "error"
        assert body["domain_code"] == "not_found"

    async def test_approve_no_longer_pending(
        self,
        fake_app_state: AppState,
        fake_approval_store: AsyncMock,
        actor: AgentIdentity,
    ) -> None:
        item = _make_item(approval_id="a1")
        fake_approval_store.get.return_value = item
        fake_approval_store.save_if_pending.return_value = None  # FWW loss
        handler = APPROVAL_HANDLERS["synthorg_approvals_approve"]

        result = await handler(
            app_state=fake_app_state,
            arguments={"approval_id": "a1"},
            actor=actor,
        )
        body = json.loads(result)
        assert body["status"] == "error"
        assert body["domain_code"] == "conflict"

    async def test_approve_requires_actor(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = APPROVAL_HANDLERS["synthorg_approvals_approve"]
        result = await handler(
            app_state=fake_app_state,
            arguments={"approval_id": "a1"},
            actor=None,
        )
        body = json.loads(result)
        assert body["status"] == "error"


# --- reject (destructive) --------------------------------------------------


class TestApprovalsReject:
    """``synthorg_approvals_reject`` is destructive -- guardrails enforced."""

    async def test_reject_happy_path(
        self,
        fake_app_state: AppState,
        fake_approval_store: AsyncMock,
        actor: AgentIdentity,
    ) -> None:
        item = _make_item(approval_id="a1")
        fake_approval_store.get.return_value = item
        fake_approval_store.save_if_pending.side_effect = lambda updated: updated
        handler = APPROVAL_HANDLERS["synthorg_approvals_reject"]

        with structlog.testing.capture_logs() as logs:
            result = await handler(
                app_state=fake_app_state,
                arguments={
                    "approval_id": "a1",
                    "reason": "violates policy X",
                    "confirm": True,
                },
                actor=actor,
            )
        body = json.loads(result)

        assert body["status"] == "ok"
        assert body["data"]["status"] == "rejected"
        assert body["data"]["decision_reason"] == "violates policy X"
        assert body["data"]["decided_by"] == str(actor.id)

        # Audit event fires with the full attribution payload.
        audit = [e for e in logs if e.get("event") == MCP_ADMIN_OP_EXECUTED]
        assert len(audit) == 1
        assert audit[0]["actor_agent_id"] == str(actor.id)
        assert audit[0]["reason"] == "violates policy X"
        assert audit[0]["target_id"] == "a1"

    async def test_reject_without_confirm(
        self,
        fake_app_state: AppState,
        actor: AgentIdentity,
    ) -> None:
        handler = APPROVAL_HANDLERS["synthorg_approvals_reject"]

        with structlog.testing.capture_logs() as logs:
            result = await handler(
                app_state=fake_app_state,
                arguments={"approval_id": "a1", "reason": "bad idea"},
                actor=actor,
            )
        body = json.loads(result)
        assert body["status"] == "error"
        assert body["domain_code"] == "guardrail_violated"
        guardrail = [
            e for e in logs if e.get("event") == MCP_HANDLER_GUARDRAIL_VIOLATED
        ]
        assert len(guardrail) == 1
        assert guardrail[0]["violation"] == "missing_confirm"
        # Audit event must NOT fire when the guardrail blocks the op;
        # a false audit trail would claim a destructive action happened
        # when it did not.
        audit_events = [e for e in logs if e.get("event") == MCP_ADMIN_OP_EXECUTED]
        assert audit_events == []

    async def test_reject_without_reason(
        self,
        fake_app_state: AppState,
        actor: AgentIdentity,
    ) -> None:
        handler = APPROVAL_HANDLERS["synthorg_approvals_reject"]
        result = await handler(
            app_state=fake_app_state,
            arguments={"approval_id": "a1", "confirm": True},
            actor=actor,
        )
        body = json.loads(result)
        assert body["status"] == "error"
        assert body["domain_code"] == "guardrail_violated"

    async def test_reject_without_actor(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = APPROVAL_HANDLERS["synthorg_approvals_reject"]
        result = await handler(
            app_state=fake_app_state,
            arguments={
                "approval_id": "a1",
                "reason": "x",
                "confirm": True,
            },
            actor=None,
        )
        body = json.loads(result)
        assert body["status"] == "error"
        assert body["domain_code"] == "guardrail_violated"

    async def test_reject_not_found(
        self,
        fake_app_state: AppState,
        fake_approval_store: AsyncMock,
        actor: AgentIdentity,
    ) -> None:
        fake_approval_store.get.return_value = None
        handler = APPROVAL_HANDLERS["synthorg_approvals_reject"]

        with structlog.testing.capture_logs() as logs:
            result = await handler(
                app_state=fake_app_state,
                arguments={
                    "approval_id": "missing",
                    "reason": "n/a",
                    "confirm": True,
                },
                actor=actor,
            )
        body = json.loads(result)
        assert body["status"] == "error"
        assert body["domain_code"] == "not_found"
        # Invoke-failed event fires; audit event must NOT (no mutation
        # was executed).
        events = {e.get("event") for e in logs}
        assert MCP_ADMIN_OP_EXECUTED not in events
        assert MCP_HANDLER_INVOKE_FAILED in events
