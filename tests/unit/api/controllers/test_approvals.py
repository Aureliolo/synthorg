"""Tests for approvals controller."""

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from litestar.datastructures import State

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers.approvals._shared import _to_approval_response
from synthorg.api.controllers.approvals.decisions import (
    ApprovalsDecisionsController,
    _decide_idempotent,
)
from synthorg.api.dto import ApproveRequest, RejectRequest
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ConflictError
from synthorg.idempotency import IdempotencyResult, IdempotencyService
from synthorg.settings.resolver import ConfigResolver
from tests._shared import (
    JsonDict,
    LoopAsyncClient,
    as_uuid,
    coerce_id,
    make_app_state,
    mock_of,
    sid,
)
from tests.unit.api.conftest import make_approval, make_auth_headers

_BASE = "/api/v1/approvals"
_WRITE_HEADERS = make_auth_headers("ceo")
_READ_HEADERS = make_auth_headers("observer")


def _idem(headers: Mapping[str, str]) -> dict[str, str]:
    """Merge a fresh required Idempotency-Key into decision request headers.

    The approve/reject endpoints require the header. A unique key per
    call keeps the shared session-scoped idempotency store from returning
    a prior test's cached decision.

    Returns:
        A new headers dict carrying a unique ``Idempotency-Key``.
    """
    return {**headers, "Idempotency-Key": str(uuid4())}


def _create_payload(
    **overrides: object,
) -> JsonDict:
    defaults: JsonDict = {
        "action_type": "code:merge",
        "title": "Merge PR #42",
        "description": "Merging feature branch",
        "risk_level": "medium",
    }
    defaults.update(overrides)
    return defaults


async def _seed_item(  # type: ignore[explicit-any]  # **kwargs forwarded verbatim to the typed make_approval factory
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
    async def test_list_empty(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get(_BASE, headers=_READ_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []

    async def test_list_with_data(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store, approval_id="a1")
        await _seed_item(approval_store, approval_id="a2")
        resp = await async_test_client.get(_BASE, headers=_READ_HEADERS)
        assert resp.status_code == 200

    async def test_list_filter_by_status(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store, approval_id="a1")
        now = datetime.now(UTC)
        approved = ApprovalItem(
            id=as_uuid("a2"),
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
        resp = await async_test_client.get(
            _BASE,
            params={"status": "pending"},
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["data"][0]["id"] == sid("a1")

    async def test_list_filter_by_risk_level(
        self,
        async_test_client: LoopAsyncClient,
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
        resp = await async_test_client.get(
            _BASE,
            params={"risk_level": "critical"},
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["data"][0]["id"] == sid("a2")

    async def test_list_pagination(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        for i in range(5):
            await _seed_item(approval_store, approval_id=f"a{i}")
        # Walk first page to obtain a cursor, then fetch the next.
        resp1 = await async_test_client.get(
            _BASE,
            params={"limit": 2},
            headers=_READ_HEADERS,
        )
        assert resp1.status_code == 200
        body1 = resp1.json()
        assert len(body1["data"]) == 2
        assert body1["pagination"]["has_more"] is True
        cursor = body1["pagination"]["next_cursor"]
        assert cursor is not None

        resp2 = await async_test_client.get(
            _BASE,
            params={"limit": 2, "cursor": cursor},
            headers=_READ_HEADERS,
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert len(body2["data"]) == 2
        assert body2["pagination"]["has_more"] is True
        cursor2 = body2["pagination"]["next_cursor"]
        assert cursor2 is not None

        # Walk to the terminal page (5 items at limit=2 -> page 3 is
        # the last page with 1 item).  ``next_cursor`` and
        # ``has_more`` must clear together per the
        # ``_validate_cursor_consistency`` model validator on
        # ``PaginationMeta``.
        resp3 = await async_test_client.get(
            _BASE,
            params={"limit": 2, "cursor": cursor2},
            headers=_READ_HEADERS,
        )
        assert resp3.status_code == 200
        body3 = resp3.json()
        assert len(body3["data"]) == 1
        assert body3["pagination"]["has_more"] is False
        assert body3["pagination"]["next_cursor"] is None

    async def test_list_blocks_no_role(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            _BASE, headers={"Authorization": "Bearer invalid-token"}
        )
        assert resp.status_code == 401


@pytest.mark.unit
class TestGetApproval:
    async def test_get_found(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store)
        resp = await async_test_client.get(
            f"{_BASE}/{sid('approval-001')}",
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == sid("approval-001")

    async def test_get_not_found(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get(
            f"{_BASE}/nonexistent",
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 404

    async def test_get_allows_observer(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        # Observer should have read access (even if 404)
        resp = await async_test_client.get(
            f"{_BASE}/nonexistent",
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 404  # 404 = authorized but not found

    async def test_get_blocks_no_role(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get(
            f"{_BASE}/whatever",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401


@pytest.mark.unit
class TestCreateApproval:
    async def test_create_valid(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.post(
            _BASE,
            json=_create_payload(),
            headers=_idem(_WRITE_HEADERS),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["status"] == "pending"
        assert str(UUID(body["data"]["id"])) == body["data"]["id"]

    async def test_create_with_ttl(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.post(
            _BASE,
            json=_create_payload(ttl_seconds=3600),
            headers=_idem(_WRITE_HEADERS),
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["expires_at"] is not None

    async def test_create_without_ttl(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.post(
            _BASE,
            json=_create_payload(),
            headers=_idem(_WRITE_HEADERS),
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["expires_at"] is None

    async def test_create_with_task_id(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            _BASE,
            json=_create_payload(task_id="task-001"),
            headers=_idem(_WRITE_HEADERS),
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["task_id"] == "task-001"

    async def test_create_with_metadata(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            _BASE,
            json=_create_payload(metadata={"pr": "42"}),
            headers=_idem(_WRITE_HEADERS),
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["metadata"] == {"pr": "42"}

    async def test_create_blocks_observer(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            _BASE,
            json=_create_payload(),
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 403

    async def test_create_blocks_no_auth(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            _BASE,
            json=_create_payload(),
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401


@pytest.mark.unit
class TestApproveApproval:
    async def test_approve_pending(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store)
        resp = await async_test_client.post(
            f"{_BASE}/{sid('approval-001')}/approve",
            json={"comment": "Looks good"},
            headers=_idem(_WRITE_HEADERS),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status"] == "approved"
        assert body["data"]["decided_by"] == "test-ceo"
        assert body["data"]["decision_reason"] == "Looks good"

    async def test_approve_records_decided_by_from_header(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store)
        resp = await async_test_client.post(
            f"{_BASE}/{sid('approval-001')}/approve",
            json={},
            headers=_idem(make_auth_headers("manager")),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["decided_by"] == "test-manager"

    async def test_approve_not_found(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.post(
            f"{_BASE}/nonexistent/approve",
            json={},
            headers=_idem(_WRITE_HEADERS),
        )
        assert resp.status_code == 404

    async def test_approve_already_decided(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        now = datetime.now(UTC)
        item = ApprovalItem(
            id=as_uuid("decided-001"),
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
        resp = await async_test_client.post(
            f"{_BASE}/{sid('decided-001')}/approve",
            json={},
            headers=_idem(_WRITE_HEADERS),
        )
        assert resp.status_code == 409

    async def test_approve_expired(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        now = datetime.now(UTC)
        item = ApprovalItem(
            id=as_uuid("expired-001"),
            action_type="code_merge",
            title="Expired",
            description="desc",
            requested_by="agent-dev",
            risk_level=ApprovalRiskLevel.LOW,
            created_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )
        # Directly insert to bypass expiry validation timing
        approval_store._items[str(item.id)] = item
        resp = await async_test_client.post(
            f"{_BASE}/{sid('expired-001')}/approve",
            json={},
            headers=_idem(_WRITE_HEADERS),
        )
        assert resp.status_code == 409

    async def test_approve_blocks_observer(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            f"{_BASE}/whatever/approve",
            json={},
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 403


@pytest.mark.unit
class TestRejectApproval:
    async def test_reject_pending(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store)
        resp = await async_test_client.post(
            f"{_BASE}/{sid('approval-001')}/reject",
            json={"reason": "Too risky"},
            headers=_idem(_WRITE_HEADERS),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status"] == "rejected"
        assert body["data"]["decided_by"] == "test-ceo"
        assert body["data"]["decision_reason"] == "Too risky"

    async def test_reject_requires_reason(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store)
        # Missing reason field should fail validation
        resp = await async_test_client.post(
            f"{_BASE}/{sid('approval-001')}/reject",
            json={},
            headers=_idem(_WRITE_HEADERS),
        )
        assert resp.status_code == 400

    async def test_reject_not_found(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.post(
            f"{_BASE}/nonexistent/reject",
            json={"reason": "nope"},
            headers=_idem(_WRITE_HEADERS),
        )
        assert resp.status_code == 404

    async def test_reject_already_decided(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        now = datetime.now(UTC)
        item = ApprovalItem(
            id=as_uuid("decided-002"),
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
        resp = await async_test_client.post(
            f"{_BASE}/{sid('decided-002')}/reject",
            json={"reason": "nope again"},
            headers=_idem(_WRITE_HEADERS),
        )
        assert resp.status_code == 409

    async def test_reject_blocks_observer(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            f"{_BASE}/whatever/reject",
            json={"reason": "nope"},
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 403


@pytest.mark.unit
class TestApprovalUrgencyFields:
    """Tests for computed seconds_remaining and urgency_level fields."""

    async def test_list_includes_urgency_fields(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store, ttl_seconds=7200)
        resp = await async_test_client.get(_BASE, headers=_READ_HEADERS)
        assert resp.status_code == 200
        item = resp.json()["data"][0]
        assert "seconds_remaining" in item
        assert "urgency_level" in item

    async def test_urgency_no_expiry(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store)
        resp = await async_test_client.get(
            f"{_BASE}/{sid('approval-001')}", headers=_READ_HEADERS
        )
        data = resp.json()["data"]
        assert data["seconds_remaining"] is None
        assert data["urgency_level"] == "no_expiry"

    @pytest.mark.parametrize(
        ("approval_id", "ttl_seconds", "expected_urgency"),
        [
            ("crit-001", 1800, "critical"),
            ("high-001", 7200, "high"),
            ("norm-001", 86400, "normal"),
        ],
    )
    async def test_urgency_level_by_ttl(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
        approval_id: str,
        ttl_seconds: int,
        expected_urgency: str,
    ) -> None:
        await _seed_item(
            approval_store,
            approval_id=approval_id,
            ttl_seconds=ttl_seconds,
        )
        resp = await async_test_client.get(
            f"{_BASE}/{coerce_id(approval_id)}",
            headers=_READ_HEADERS,
        )
        data = resp.json()["data"]
        assert data["urgency_level"] == expected_urgency
        assert data["seconds_remaining"] is not None


@pytest.mark.unit
class TestApprovalIdempotency:
    """Idempotency-Key contract on the approve / reject decision endpoints."""

    async def test_approve_missing_idempotency_key_rejected(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store)
        # No Idempotency-Key header: the required HeaderParameter rejects
        # the request before any decision runs.
        resp = await async_test_client.post(
            f"{_BASE}/{sid('approval-001')}/approve",
            json={},
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 400

    async def test_reject_missing_idempotency_key_rejected(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store)
        resp = await async_test_client.post(
            f"{_BASE}/{sid('approval-001')}/reject",
            json={"reason": "nope"},
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 400

    @pytest.mark.parametrize(
        ("endpoint", "scope", "data"),
        [
            ("approve", "approval:approve", ApproveRequest()),
            ("reject", "approval:reject", RejectRequest(reason="no")),
        ],
    )
    async def test_decision_binds_resource_scoped_idempotency_key(
        self,
        endpoint: str,
        scope: str,
        data: ApproveRequest | RejectRequest,
    ) -> None:
        # The dedup key MUST bind the approval id so the same caller token
        # reused against a different approval cannot return this one's
        # cached decision. Capture the (scope, key) the controller forwards
        # to ``run_idempotent`` without running the callback (the canned
        # result short-circuits the decision body).
        captured: dict[str, str] = {}
        item = make_approval(approval_id="bind-1")
        canned = _to_approval_response(
            item,
            now=datetime.now(UTC),
            urgency_critical_seconds=3600.0,
            urgency_high_seconds=7200.0,
        ).model_dump(mode="json")

        async def _capture(
            *,
            scope: str,
            key: str,
            callback: Callable[[], Awaitable[object]],
        ) -> IdempotencyResult:
            # The callback (the real decision body) is intentionally not
            # invoked: this test pins the key shape, not the decision.
            del callback
            captured["scope"] = scope
            captured["key"] = key
            return IdempotencyResult(result=canned, fresh=True, timed_out=False)

        service: IdempotencyService = mock_of[IdempotencyService](
            run_idempotent=_capture,
        )
        # ``self`` is unused by the decision body; a spec'd mock stands in
        # so the raw ``.fn`` can be invoked without instantiating the
        # litestar-managed controller (mirrors the webhook ingest tests).
        controller_self = MagicMock(spec=ApprovalsDecisionsController)
        method = getattr(ApprovalsDecisionsController, endpoint).fn

        with patch(
            "synthorg.api.controllers.approvals.decisions.idempotency_service_of",
            return_value=service,
        ):
            await method(
                controller_self,
                state=State({"app_state": make_app_state()}),
                approval_id=sid("bind-1"),
                data=data,
                request=MagicMock(spec=object),
                idempotency_key="raw-token",
            )

        assert captured["scope"] == scope
        assert captured["key"] == f"{sid('bind-1')}:raw-token"

    async def test_decide_idempotent_maps_timed_out_to_conflict(self) -> None:
        # A concurrent in-flight claim that never resolves surfaces as
        # ``timed_out``; ``_decide_idempotent`` must translate that into a
        # 409 ConflictError rather than validating a ``None`` result.
        service: IdempotencyService = mock_of[IdempotencyService](
            run_idempotent=AsyncMock(
                return_value=IdempotencyResult(
                    result=None, fresh=False, timed_out=True
                ),
            ),
        )

        async def _never_called() -> dict[str, object]:
            msg = "callback must not run when the claim is in-flight"
            raise AssertionError(msg)

        with (
            patch(
                "synthorg.api.controllers.approvals.decisions.idempotency_service_of",
                return_value=service,
            ),
            pytest.raises(ConflictError),
        ):
            await _decide_idempotent(
                make_app_state(),
                scope="approval:approve",
                key="a1:key",
                endpoint="approvals.approve",
                decide=_never_called,
            )

    async def test_create_approval_includes_urgency(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            _BASE,
            json=_create_payload(ttl_seconds=600),
            headers=_idem(_WRITE_HEADERS),
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["urgency_level"] == "critical"
        assert data["seconds_remaining"] is not None

    async def test_approve_includes_urgency(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store)
        resp = await async_test_client.post(
            f"{_BASE}/{sid('approval-001')}/approve",
            json={"comment": "ok"},
            headers=_idem(_WRITE_HEADERS),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["urgency_level"] == "no_expiry"
        assert data["seconds_remaining"] is None

    async def test_get_approval_includes_urgency(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        # Create an approval first
        resp = await async_test_client.post(
            _BASE,
            json=_create_payload(),
            headers=_idem(_WRITE_HEADERS),
        )
        approval_id = resp.json()["data"]["id"]
        resp = await async_test_client.get(
            f"{_BASE}/{coerce_id(approval_id)}",
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "urgency_level" in data
        assert "seconds_remaining" in data

    async def test_reject_includes_urgency(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store, approval_id="rej-001")
        resp = await async_test_client.post(
            f"{_BASE}/{sid('rej-001')}/reject",
            json={"reason": "Too risky"},
            headers=_idem(_WRITE_HEADERS),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["urgency_level"] == "no_expiry"
        assert data["seconds_remaining"] is None

    @pytest.mark.parametrize(
        ("approval_id", "boundary_seconds", "expected_urgency"),
        [
            # The thresholds are inclusive ("at or below"): a TTL
            # exactly at the configured value belongs to the more
            # urgent bucket, not the next-laxer one.
            ("boundary-1h", 3600, "critical"),
            ("boundary-4h", 14400, "high"),
        ],
    )
    async def test_urgency_boundary(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
        approval_id: str,
        boundary_seconds: int,
        expected_urgency: str,
    ) -> None:
        frozen_now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        item = ApprovalItem(
            id=as_uuid(approval_id),
            action_type="code_merge",
            title="Boundary test",
            description="desc",
            requested_by="agent-dev",
            risk_level=ApprovalRiskLevel.MEDIUM,
            created_at=frozen_now,
            expires_at=frozen_now + timedelta(seconds=boundary_seconds),
        )
        await approval_store.add(item)
        with patch(
            "synthorg.api.controllers.approvals.query.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = frozen_now
            mock_dt.side_effect = datetime
            resp = await async_test_client.get(
                f"{_BASE}/{coerce_id(approval_id)}",
                headers=_READ_HEADERS,
            )
        data = resp.json()["data"]
        assert data["urgency_level"] == expected_urgency

    async def test_expired_approval_shows_zero_seconds(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        now = datetime.now(UTC)
        item = ApprovalItem(
            id=as_uuid("exp-001"),
            action_type="code_merge",
            title="Expired item",
            description="desc",
            requested_by="agent-dev",
            risk_level=ApprovalRiskLevel.LOW,
            created_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )
        approval_store._items[str(item.id)] = item
        resp = await async_test_client.get(
            f"{_BASE}/{sid('exp-001')}",
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["seconds_remaining"] == 0.0
        assert data["urgency_level"] == "critical"


@pytest.mark.unit
class TestBoardMemberApprovalAccess:
    """Board members can approve/reject but not create approvals."""

    async def test_board_member_can_approve(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store, approval_id="bm-approve-001")
        resp = await async_test_client.post(
            f"{_BASE}/{sid('bm-approve-001')}/approve",
            json={"comment": "Approved by board"},
            headers=_idem(make_auth_headers("board_member")),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "approved"
        assert resp.json()["data"]["decided_by"] == "test-board_member"

    async def test_board_member_can_reject(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await _seed_item(approval_store, approval_id="bm-reject-001")
        resp = await async_test_client.post(
            f"{_BASE}/{sid('bm-reject-001')}/reject",
            json={"reason": "Board disagrees"},
            headers=_idem(make_auth_headers("board_member")),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "rejected"

    async def test_board_member_cannot_create(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            _BASE,
            json=_create_payload(),
            headers=make_auth_headers("board_member"),
        )
        assert resp.status_code == 403


@pytest.mark.unit
class TestApprovalPathParamValidation:
    async def test_oversized_approval_id_rejected(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        long_id = "x" * 129
        resp = await async_test_client.get(
            f"{_BASE}/{long_id}",
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 400

    async def test_oversized_action_type_query_rejected(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        long_action = "x" * 129
        resp = await async_test_client.get(
            _BASE,
            params={"action_type": long_action},
            headers=_READ_HEADERS,
        )
        assert resp.status_code in (400, 422)


@pytest.mark.unit
class TestResolveUrgencyThresholdsFallback:
    """``_resolve_urgency_thresholds`` settings-backend fallback contract.

    The helper falls back to
    ``(_URGENCY_CRITICAL_FALLBACK_SECONDS, _URGENCY_HIGH_FALLBACK_SECONDS)``
    when the config resolver is unavailable or raises.  These tests
    guard that contract so a settings backend outage cannot turn every
    pending approval into a 500 -- urgency calculations degrade
    gracefully to the registry defaults.
    """

    @pytest.mark.parametrize(
        "scenario",
        ["resolver_absent", "resolver_raises"],
    )
    async def test_returns_fallback_for_absent_or_error_resolver(
        self,
        scenario: str,
    ) -> None:
        """Both fallback paths return registry defaults.

        ``resolver_absent``: ``app_state.has_config_resolver`` is
        ``False`` (no settings backend wired -- treated identically to
        a transient outage so the recovery log can fire when one
        appears).

        ``resolver_raises``: backend is wired but
        ``get_float`` raises a generic ``RuntimeError``.
        """
        from unittest.mock import AsyncMock as _AsyncMock

        from synthorg.api.controllers.approvals._shared import (
            _resolve_urgency_thresholds,
        )

        if scenario == "resolver_absent":
            app_state = make_app_state()
        else:
            app_state = make_app_state(
                config_resolver=mock_of[ConfigResolver](
                    get_float=_AsyncMock(
                        side_effect=RuntimeError("settings backend down"),
                    ),
                ),
            )
        critical, high = await _resolve_urgency_thresholds(app_state)
        assert critical == 3600.0  # _URGENCY_CRITICAL_FALLBACK_SECONDS
        assert high == 14400.0  # _URGENCY_HIGH_FALLBACK_SECONDS

    async def test_propagates_cancelled_error(self) -> None:
        import asyncio as _asyncio
        from unittest.mock import AsyncMock as _AsyncMock

        from synthorg.api.controllers.approvals._shared import (
            _resolve_urgency_thresholds,
        )

        app_state = make_app_state(
            config_resolver=mock_of[ConfigResolver](
                get_float=_AsyncMock(side_effect=_asyncio.CancelledError()),
            ),
        )
        with pytest.raises(_asyncio.CancelledError):
            await _resolve_urgency_thresholds(app_state)

    async def test_returns_resolved_values_on_success(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock

        from synthorg.api.controllers.approvals._shared import (
            _resolve_urgency_thresholds,
        )

        app_state = make_app_state(
            config_resolver=mock_of[ConfigResolver](
                get_float=_AsyncMock(side_effect=[600.0, 7200.0]),
            ),
        )
        critical, high = await _resolve_urgency_thresholds(app_state)
        assert critical == 600.0
        assert high == 7200.0

    @pytest.mark.parametrize(
        ("critical_val", "high_val"),
        [
            pytest.param(-1.0, 14400.0, id="negative-critical"),
            pytest.param(3600.0, -1.0, id="negative-high"),
            pytest.param(float("nan"), 14400.0, id="nan-critical"),
            pytest.param(3600.0, float("inf"), id="inf-high"),
            pytest.param(14400.0, 14400.0, id="critical-equals-high"),
            pytest.param(14400.0, 3600.0, id="critical-greater-than-high"),
        ],
    )
    async def test_invalid_resolved_values_fall_back(
        self,
        critical_val: float,
        high_val: float,
    ) -> None:
        """Invalid threshold pairs (negative, NaN/Inf, mis-ordered)
        return the registry fallbacks instead of being trusted.
        """
        from unittest.mock import AsyncMock as _AsyncMock

        from synthorg.api.controllers.approvals._shared import (
            _resolve_urgency_thresholds,
        )

        app_state = make_app_state(
            config_resolver=mock_of[ConfigResolver](
                get_float=_AsyncMock(side_effect=[critical_val, high_val]),
            ),
        )
        critical, high = await _resolve_urgency_thresholds(app_state)
        assert critical == 3600.0  # _URGENCY_CRITICAL_FALLBACK_SECONDS
        assert high == 14400.0  # _URGENCY_HIGH_FALLBACK_SECONDS

    async def test_recovery_log_after_fallback(self) -> None:
        """A failure-then-success sequence emits the recovery signal.

        The first call raises (forces fallback + arms the recovery
        flag) and the second returns valid values; the recovery log
        ``API_SETTINGS_BACKEND_RECOVERED`` must fire on the second
        call, and the helper must return the resolved values.
        """
        from unittest.mock import AsyncMock as _AsyncMock

        import structlog.testing

        from synthorg.api.controllers.approvals._shared import (
            _resolve_urgency_thresholds,
        )
        from synthorg.observability.events.api import API_SETTINGS_BACKEND_RECOVERED

        # First call: raise.  Second + third: serve a valid pair
        # (each ``_resolve_urgency_thresholds`` call awaits ``get_float``
        # twice, once per threshold).
        app_state = make_app_state(
            config_resolver=mock_of[ConfigResolver](
                get_float=_AsyncMock(
                    side_effect=[
                        RuntimeError("settings backend down"),
                        600.0,
                        7200.0,
                    ],
                ),
            ),
        )
        # First call: forces fallback.
        await _resolve_urgency_thresholds(app_state)
        # Second call: succeeds and emits the recovery event.
        with structlog.testing.capture_logs() as cap:
            critical, high = await _resolve_urgency_thresholds(app_state)
        events = [e["event"] for e in cap]
        assert API_SETTINGS_BACKEND_RECOVERED in events
        assert critical == 600.0
        assert high == 7200.0
