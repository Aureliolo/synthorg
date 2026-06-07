"""Unit tests for SQLite approval repository."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import aiosqlite
import pytest

from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.persistence.approval_protocol import ApprovalFilterSpec
from synthorg.persistence.sqlite.approval_repo import SQLiteApprovalRepository
from tests._shared import as_uuid, sid
from tests._shared.persistence import make_private_write_context

pytestmark = pytest.mark.unit

_CREATE_TABLE = """
    CREATE TABLE approvals (
        id TEXT NOT NULL PRIMARY KEY,
        action_type TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        requested_by TEXT NOT NULL,
        risk_level TEXT NOT NULL DEFAULT 'medium',
        source TEXT NOT NULL DEFAULT 'review_gate',
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        expires_at TEXT,
        decided_at TEXT,
        decided_by TEXT,
        decision_reason TEXT,
        task_id TEXT,
        evidence_package TEXT,
        metadata TEXT NOT NULL DEFAULT '{}',
        consumed_at TEXT
    )
"""


def _item(
    *,
    item_id: str = "approval-1",
    status: ApprovalStatus = ApprovalStatus.PENDING,
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.MEDIUM,
    action_type: str = "meta.config_tuning",
) -> ApprovalItem:
    now = datetime.now(UTC)
    extra: dict[str, object] = {}
    if status in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
        extra["decided_at"] = now
        extra["decided_by"] = "test-admin"
        if status == ApprovalStatus.REJECTED:
            extra["decision_reason"] = "rejected for test"
    return ApprovalItem(
        id=as_uuid(item_id),
        action_type=action_type,
        title="Test Proposal",
        description="Test description",
        requested_by="test-agent",
        risk_level=risk_level,
        status=status,
        created_at=now,
        **extra,  # type: ignore[arg-type]
    )


@pytest.fixture
async def repo() -> AsyncGenerator[SQLiteApprovalRepository]:
    """In-memory SQLite repo for testing."""
    db = await aiosqlite.connect(":memory:")
    await db.execute(_CREATE_TABLE)
    await db.commit()
    repo = SQLiteApprovalRepository(db, write_context=make_private_write_context())
    yield repo
    await db.close()


class TestSQLiteApprovalRepository:
    """Approval repository CRUD tests."""

    async def test_save_and_get(self, repo: SQLiteApprovalRepository) -> None:
        item = _item()
        await repo.save(item)
        fetched = await repo.get(str(item.id))
        assert fetched is not None
        assert fetched.id == item.id
        assert fetched.action_type == item.action_type
        assert fetched.status == ApprovalStatus.PENDING

    async def test_get_nonexistent_returns_none(
        self,
        repo: SQLiteApprovalRepository,
    ) -> None:
        result = await repo.get(sid("nonexistent"))
        assert result is None

    async def test_save_updates_existing(
        self,
        repo: SQLiteApprovalRepository,
    ) -> None:
        item = _item()
        await repo.save(item)
        updated = item.model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_by": "admin",
                "decided_at": datetime.now(UTC),
                "decision_reason": "Looks good",
            },
        )
        await repo.save(updated)
        fetched = await repo.get(str(item.id))
        assert fetched is not None
        assert fetched.status == ApprovalStatus.APPROVED
        assert fetched.decided_by == "admin"

    async def test_list_items_no_filter(
        self,
        repo: SQLiteApprovalRepository,
    ) -> None:
        await repo.save(_item(item_id="a1"))
        await repo.save(_item(item_id="a2"))
        items = await repo.list_items()
        assert len(items) == 2

    async def test_list_items_filter_status(
        self,
        repo: SQLiteApprovalRepository,
    ) -> None:
        await repo.save(_item(item_id="a1", status=ApprovalStatus.PENDING))
        await repo.save(_item(item_id="a2", status=ApprovalStatus.APPROVED))
        pending = await repo.query(ApprovalFilterSpec(status=ApprovalStatus.PENDING))
        assert len(pending) == 1
        assert pending[0].id == as_uuid("a1")

    async def test_list_items_filter_risk_level(
        self,
        repo: SQLiteApprovalRepository,
    ) -> None:
        await repo.save(
            _item(item_id="a1", risk_level=ApprovalRiskLevel.HIGH),
        )
        await repo.save(
            _item(item_id="a2", risk_level=ApprovalRiskLevel.LOW),
        )
        high = await repo.query(ApprovalFilterSpec(risk_level=ApprovalRiskLevel.HIGH))
        assert len(high) == 1
        assert high[0].id == as_uuid("a1")

    async def test_list_items_filter_action_type(
        self,
        repo: SQLiteApprovalRepository,
    ) -> None:
        await repo.save(
            _item(item_id="a1", action_type="meta.config_tuning"),
        )
        await repo.save(
            _item(item_id="a2", action_type="meta.architecture"),
        )
        config = await repo.query(ApprovalFilterSpec(action_type="meta.config_tuning"))
        assert len(config) == 1
        assert config[0].id == as_uuid("a1")

    async def test_delete_existing(
        self,
        repo: SQLiteApprovalRepository,
    ) -> None:
        item = _item()
        await repo.save(item)
        deleted = await repo.delete(str(item.id))
        assert deleted is True
        assert await repo.get(str(item.id)) is None

    async def test_delete_nonexistent(
        self,
        repo: SQLiteApprovalRepository,
    ) -> None:
        deleted = await repo.delete(sid("nonexistent"))
        assert deleted is False

    async def test_metadata_roundtrip(
        self,
        repo: SQLiteApprovalRepository,
    ) -> None:
        item = _item()
        item = item.model_copy(
            update={"metadata": {"key": "value", "num": "42"}},
        )
        await repo.save(item)
        fetched = await repo.get(str(item.id))
        assert fetched is not None
        assert fetched.metadata == {"key": "value", "num": "42"}
