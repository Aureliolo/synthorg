"""Tests for approvals controller."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from litestar.testing import TestClient

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus
from tests.unit.api.conftest import make_approval, make_auth_headers

_BASE = "/api/v1/approvals"
_WRITE_HEADERS = make_auth_headers("ceo")
_READ_HEADERS = make_auth_headers("observer")


def _create_payload(
    **overrides: Any,
) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "action_type": "code:merge",
        "title": "Merge PR #42",
        "description": "Merging feature branch",
        "risk_level": "medium",
    }
    defaults.update(overrides)
    return defaults


async def _seed_item(
    store: ApprovalStore,
    *,
    approval_id: str = "approval-001",
    **kwargs: Any,
) -> ApprovalItem:
    item = make_approval(approval_id=approval_id, **kwargs)
    await store.add(item)
    return item


@pytest.mark.unit
class TestListApprovals:
    def test_list_empty(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get(_BASE, headers=_READ_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []
        assert body["pagination"]["total"] == 0

    async def test_list_with_data(
        self,
        test_client: TestClient[Any],
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store, approval_id="a1")
        await _seed_item(approval_store, approval_id="a2")
        resp = test_client.get(_BASE, headers=_READ_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total"] == 2

    async def test_list_filter_by_status(
        self,
        test_client: TestClient[Any],
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store, approval_id="a1")
        now = datetime.now(UTC)
        approved = ApprovalItem(
            id="a2",
            action_type="deployment",
            title="Deploy",
            description="desc",
            requested_by="agent-ops",
            risk_level=ApprovalRiskLevel.HIGH,
            status=ApprovalStatus.APPROVED,
            created_at=now,
            decided_at=now + timedelta(minutes=1),
            decided_by="ceo",
        )
        await approval_store.add(approved)
        resp = test_client.get(
            _BASE,
            params={"status": "pending"},
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total"] == 1
        assert resp.json()["data"][0]["id"] == "a1"

    async def test_list_filter_by_risk_level(
        self,
        test_client: TestClient[Any],
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(
            approval_store,
            approval_id="a1",
            risk_level=ApprovalRiskLevel.LOW,
        )
        await _seed_item(
            approval_store,
            approval_id="a2",
            risk_level=ApprovalRiskLevel.CRITICAL,
        )
        resp = test_client.get(
            _BASE,
            params={"risk_level": "critical"},
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total"] == 1
        assert resp.json()["data"][0]["id"] == "a2"

    async def test_list_pagination(
        self,
        test_client: TestClient[Any],
        approval_store: ApprovalStore,
    ) -> None:
        for i in range(5):
            await _seed_item(approval_store, approval_id=f"a{i}")
        resp = test_client.get(
            _BASE,
            params={"offset": 2, "limit": 2},
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["pagination"]["total"] == 5
        assert body["pagination"]["offset"] == 2

    def test_list_blocks_no_role(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get(_BASE, headers={"Authorization": "Bearer invalid-token"})
        assert resp.status_code == 401


@pytest.mark.unit
class TestGetApproval:
    async def test_get_found(
        self,
        test_client: TestClient[Any],
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store)
        resp = test_client.get(
            f"{_BASE}/approval-001",
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == "approval-001"

    def test_get_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get(
            f"{_BASE}/nonexistent",
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 404

    def test_get_allows_observer(self, test_client: TestClient[Any]) -> None:
        # Observer should have read access (even if 404)
        resp = test_client.get(
            f"{_BASE}/nonexistent",
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 404  # 404 = authorized but not found

    def test_get_blocks_no_role(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get(
            f"{_BASE}/whatever",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401


@pytest.mark.unit
class TestCreateApproval:
    def test_create_valid(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post(
            _BASE,
            json=_create_payload(),
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["status"] == "pending"
        assert body["data"]["id"].startswith("approval-")

    def test_create_with_ttl(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post(
            _BASE,
            json=_create_payload(ttl_seconds=3600),
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["expires_at"] is not None

    def test_create_without_ttl(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post(
            _BASE,
            json=_create_payload(),
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["expires_at"] is None

    def test_create_with_task_id(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post(
            _BASE,
            json=_create_payload(task_id="task-001"),
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["task_id"] == "task-001"

    def test_create_with_metadata(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post(
            _BASE,
            json=_create_payload(metadata={"pr": "42"}),
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["metadata"] == {"pr": "42"}

    def test_create_blocks_observer(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post(
            _BASE,
            json=_create_payload(),
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 403

    def test_create_blocks_no_auth(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post(
            _BASE,
            json=_create_payload(),
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401


@pytest.mark.unit
class TestApproveApproval:
    async def test_approve_pending(
        self,
        test_client: TestClient[Any],
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store)
        resp = test_client.post(
            f"{_BASE}/approval-001/approve",
            json={"comment": "Looks good"},
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status"] == "approved"
        assert body["data"]["decided_by"] == "test-ceo"
        assert body["data"]["decision_reason"] == "Looks good"

    async def test_approve_records_decided_by_from_header(
        self,
        test_client: TestClient[Any],
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store)
        resp = test_client.post(
            f"{_BASE}/approval-001/approve",
            json={},
            headers=make_auth_headers("manager"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["decided_by"] == "test-manager"

    def test_approve_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post(
            f"{_BASE}/nonexistent/approve",
            json={},
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 404

    async def test_approve_already_decided(
        self,
        test_client: TestClient[Any],
        approval_store: ApprovalStore,
    ) -> None:
        now = datetime.now(UTC)
        item = ApprovalItem(
            id="decided-001",
            action_type="code_merge",
            title="Already decided",
            description="desc",
            requested_by="agent-dev",
            risk_level=ApprovalRiskLevel.MEDIUM,
            status=ApprovalStatus.APPROVED,
            created_at=now,
            decided_at=now + timedelta(minutes=1),
            decided_by="ceo",
        )
        await approval_store.add(item)
        resp = test_client.post(
            f"{_BASE}/decided-001/approve",
            json={},
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 409

    async def test_approve_expired(
        self,
        test_client: TestClient[Any],
        approval_store: ApprovalStore,
    ) -> None:
        now = datetime.now(UTC)
        item = ApprovalItem(
            id="expired-001",
            action_type="code_merge",
            title="Expired",
            description="desc",
            requested_by="agent-dev",
            risk_level=ApprovalRiskLevel.LOW,
            created_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )
        # Directly insert to bypass expiry validation timing
        approval_store._items[item.id] = item
        resp = test_client.post(
            f"{_BASE}/expired-001/approve",
            json={},
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 409

    def test_approve_blocks_observer(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post(
            f"{_BASE}/whatever/approve",
            json={},
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 403


@pytest.mark.unit
class TestRejectApproval:
    async def test_reject_pending(
        self,
        test_client: TestClient[Any],
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store)
        resp = test_client.post(
            f"{_BASE}/approval-001/reject",
            json={"reason": "Too risky"},
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status"] == "rejected"
        assert body["data"]["decided_by"] == "test-ceo"
        assert body["data"]["decision_reason"] == "Too risky"

    async def test_reject_requires_reason(
        self,
        test_client: TestClient[Any],
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store)
        # Missing reason field should fail validation
        resp = test_client.post(
            f"{_BASE}/approval-001/reject",
            json={},
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 400

    def test_reject_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post(
            f"{_BASE}/nonexistent/reject",
            json={"reason": "nope"},
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 404

    async def test_reject_already_decided(
        self,
        test_client: TestClient[Any],
        approval_store: ApprovalStore,
    ) -> None:
        now = datetime.now(UTC)
        item = ApprovalItem(
            id="decided-002",
            action_type="code_merge",
            title="Already rejected",
            description="desc",
            requested_by="agent-dev",
            risk_level=ApprovalRiskLevel.MEDIUM,
            status=ApprovalStatus.REJECTED,
            created_at=now,
            decided_at=now + timedelta(minutes=1),
            decided_by="ceo",
            decision_reason="Previous rejection",
        )
        await approval_store.add(item)
        resp = test_client.post(
            f"{_BASE}/decided-002/reject",
            json={"reason": "nope again"},
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 409

    def test_reject_blocks_observer(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post(
            f"{_BASE}/whatever/reject",
            json={"reason": "nope"},
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 403
