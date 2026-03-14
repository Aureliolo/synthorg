"""Tests for the in-memory ApprovalStore."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.errors import ConflictError
from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus


def _now() -> datetime:
    return datetime.now(UTC)


def _make_item(  # noqa: PLR0913
    *,
    approval_id: str = "approval-001",
    action_type: str = "code:merge",
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.MEDIUM,
    status: ApprovalStatus = ApprovalStatus.PENDING,
    ttl_seconds: int | None = None,
    decided_at: datetime | None = None,
    decided_by: str | None = None,
    decision_reason: str | None = None,
) -> ApprovalItem:
    now = _now()
    expires_at = None
    if ttl_seconds is not None:
        expires_at = now + timedelta(seconds=ttl_seconds)
    return ApprovalItem(
        id=approval_id,
        action_type=action_type,
        title="Test approval",
        description="A test approval item",
        requested_by="agent-dev",
        risk_level=risk_level,
        status=status,
        created_at=now,
        expires_at=expires_at,
        decided_at=decided_at,
        decided_by=decided_by,
        decision_reason=decision_reason,
    )


@pytest.mark.unit
@pytest.mark.timeout(30)
class TestApprovalStore:
    async def test_add_and_get_roundtrip(self) -> None:
        store = ApprovalStore()
        item = _make_item()
        await store.add(item)
        result = await store.get("approval-001")
        assert result is not None
        assert result.id == "approval-001"

    async def test_duplicate_id_raises_conflict(self) -> None:
        store = ApprovalStore()
        item = _make_item()
        await store.add(item)
        with pytest.raises(ConflictError, match="already exists"):
            await store.add(item)

    async def test_get_nonexistent_returns_none(self) -> None:
        store = ApprovalStore()
        result = await store.get("nonexistent")
        assert result is None

    async def test_list_empty(self) -> None:
        store = ApprovalStore()
        result = await store.list_items()
        assert result == ()

    async def test_list_with_status_filter(self) -> None:
        store = ApprovalStore()
        await store.add(_make_item(approval_id="a1"))
        now = _now()
        await store.add(
            _make_item(
                approval_id="a2",
                status=ApprovalStatus.APPROVED,
                decided_at=now,
                decided_by="ceo",
            ),
        )
        pending = await store.list_items(status=ApprovalStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].id == "a1"

    async def test_list_with_risk_level_filter(self) -> None:
        store = ApprovalStore()
        await store.add(
            _make_item(
                approval_id="a1",
                risk_level=ApprovalRiskLevel.LOW,
            ),
        )
        await store.add(
            _make_item(
                approval_id="a2",
                risk_level=ApprovalRiskLevel.CRITICAL,
            ),
        )
        critical = await store.list_items(risk_level=ApprovalRiskLevel.CRITICAL)
        assert len(critical) == 1
        assert critical[0].id == "a2"

    async def test_list_with_action_type_filter(self) -> None:
        store = ApprovalStore()
        await store.add(
            _make_item(approval_id="a1", action_type="code:merge"),
        )
        await store.add(
            _make_item(approval_id="a2", action_type="deploy:staging"),
        )
        merges = await store.list_items(action_type="code:merge")
        assert len(merges) == 1
        assert merges[0].id == "a1"

    async def test_lazy_expiration_on_get(self) -> None:
        store = ApprovalStore()
        now = _now()
        item = ApprovalItem(
            id="exp-001",
            action_type="code:merge",
            title="Test",
            description="desc",
            requested_by="agent-dev",
            risk_level=ApprovalRiskLevel.LOW,
            created_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )
        # Directly insert to bypass expiry check at creation
        store._items[item.id] = item
        result = await store.get("exp-001")
        assert result is not None
        assert result.status == ApprovalStatus.EXPIRED

    async def test_save_updates_item(self) -> None:
        store = ApprovalStore()
        item = _make_item()
        await store.add(item)
        now = _now()
        updated = item.model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": now,
                "decided_by": "ceo",
            },
        )
        result = await store.save(updated)
        assert result is not None
        assert result.status == ApprovalStatus.APPROVED
        fetched = await store.get("approval-001")
        assert fetched is not None
        assert fetched.status == ApprovalStatus.APPROVED

    async def test_save_nonexistent_returns_none(self) -> None:
        store = ApprovalStore()
        now = _now()
        item = ApprovalItem(
            id="nonexistent",
            action_type="code:merge",
            title="Test",
            description="desc",
            requested_by="agent-dev",
            risk_level=ApprovalRiskLevel.LOW,
            status=ApprovalStatus.APPROVED,
            created_at=now,
            decided_at=now + timedelta(minutes=1),
            decided_by="ceo",
        )
        result = await store.save(item)
        assert result is None


@pytest.mark.unit
@pytest.mark.timeout(30)
class TestSaveIfPending:
    """save_if_pending() optimistic concurrency guard."""

    async def test_saves_when_pending(self) -> None:
        store = ApprovalStore()
        item = _make_item()
        await store.add(item)

        updated = item.model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": _now(),
                "decided_by": "admin",
            },
        )
        result = await store.save_if_pending(updated)
        assert result is not None
        assert result.status == ApprovalStatus.APPROVED

    async def test_returns_none_when_already_decided(self) -> None:
        store = ApprovalStore()
        now = _now()
        item = _make_item(
            status=ApprovalStatus.APPROVED,
            decided_at=now,
            decided_by="admin",
        )
        await store.add(item)

        updated = item.model_copy(
            update={"status": ApprovalStatus.REJECTED},
        )
        result = await store.save_if_pending(updated)
        assert result is None

    async def test_returns_none_when_not_found(self) -> None:
        store = ApprovalStore()
        item = _make_item(approval_id="nonexistent")
        result = await store.save_if_pending(item)
        assert result is None

    async def test_returns_none_when_expired(self) -> None:
        store = ApprovalStore()
        now = _now()
        item = ApprovalItem(
            id="exp-001",
            action_type="code:merge",
            title="Test",
            description="desc",
            requested_by="agent-dev",
            risk_level=ApprovalRiskLevel.LOW,
            created_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )
        store._items[item.id] = item
        updated = item.model_copy(
            update={"status": ApprovalStatus.APPROVED},
        )
        result = await store.save_if_pending(updated)
        assert result is None


@pytest.mark.unit
@pytest.mark.timeout(30)
class TestApprovalStoreFilters:
    """Combined filter tests."""

    async def test_combined_status_and_risk(self) -> None:
        store = ApprovalStore()
        await store.add(_make_item(approval_id="a1", risk_level=ApprovalRiskLevel.HIGH))
        await store.add(
            _make_item(
                approval_id="a2",
                risk_level=ApprovalRiskLevel.LOW,
            ),
        )
        await store.add(
            _make_item(
                approval_id="a3",
                risk_level=ApprovalRiskLevel.HIGH,
                status=ApprovalStatus.APPROVED,
                decided_at=_now(),
                decided_by="admin",
            ),
        )
        result = await store.list_items(
            status=ApprovalStatus.PENDING,
            risk_level=ApprovalRiskLevel.HIGH,
        )
        assert len(result) == 1
        assert result[0].id == "a1"

    async def test_combined_status_risk_action(self) -> None:
        store = ApprovalStore()
        await store.add(
            _make_item(
                approval_id="a1",
                action_type="deploy:prod",
                risk_level=ApprovalRiskLevel.CRITICAL,
            ),
        )
        await store.add(
            _make_item(
                approval_id="a2",
                action_type="db:admin",
                risk_level=ApprovalRiskLevel.CRITICAL,
            ),
        )
        result = await store.list_items(
            status=ApprovalStatus.PENDING,
            risk_level=ApprovalRiskLevel.CRITICAL,
            action_type="deploy:prod",
        )
        assert len(result) == 1
        assert result[0].id == "a1"

    async def test_no_matches_returns_empty(self) -> None:
        store = ApprovalStore()
        await store.add(_make_item())
        result = await store.list_items(
            status=ApprovalStatus.REJECTED,
        )
        assert result == ()


@pytest.mark.unit
@pytest.mark.timeout(30)
class TestOnExpireCallback:
    """on_expire callback lifecycle."""

    async def test_callback_receives_expired_item(self) -> None:
        callback = MagicMock()
        store = ApprovalStore(on_expire=callback)
        now = _now()
        item = ApprovalItem(
            id="exp-001",
            action_type="code:merge",
            title="Test",
            description="desc",
            requested_by="agent-dev",
            risk_level=ApprovalRiskLevel.LOW,
            created_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )
        store._items[item.id] = item
        await store.get("exp-001")
        callback.assert_called_once()
        expired_item = callback.call_args[0][0]
        assert expired_item.status == ApprovalStatus.EXPIRED

    async def test_callback_exception_does_not_prevent_expiration(self) -> None:
        callback = MagicMock(side_effect=RuntimeError("oops"))
        store = ApprovalStore(on_expire=callback)
        now = _now()
        item = ApprovalItem(
            id="exp-001",
            action_type="code:merge",
            title="Test",
            description="desc",
            requested_by="agent-dev",
            risk_level=ApprovalRiskLevel.LOW,
            created_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )
        store._items[item.id] = item
        result = await store.get("exp-001")
        assert result is not None
        assert result.status == ApprovalStatus.EXPIRED

    async def test_expired_items_have_expired_status_in_list(self) -> None:
        store = ApprovalStore()
        now = _now()
        live = _make_item(approval_id="live")
        expired = ApprovalItem(
            id="expired",
            action_type="code:merge",
            title="Test",
            description="desc",
            requested_by="agent-dev",
            risk_level=ApprovalRiskLevel.LOW,
            created_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )
        await store.add(live)
        store._items[expired.id] = expired

        # All items returned, but expired ones have EXPIRED status
        items = await store.list_items()
        assert len(items) == 2
        statuses = {i.id: i.status for i in items}
        assert statuses["live"] == ApprovalStatus.PENDING
        assert statuses["expired"] == ApprovalStatus.EXPIRED

        # Filter to pending only excludes expired
        pending = await store.list_items(status=ApprovalStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].id == "live"
