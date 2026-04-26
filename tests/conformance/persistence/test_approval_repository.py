"""Conformance tests for ``ApprovalRepository`` (SQLite + Postgres).

The approval repository is not exposed on ``PersistenceBackend``
(``ApprovalStore`` wires it directly), so this file builds the
backend-specific concrete repo over the migrated ``backend.get_db()``
handle.  Both arms exercise the same protocol surface so SQLite and
Postgres divergence (TEXT vs JSONB ``metadata`` / ``evidence_package``,
TEXT vs TIMESTAMPTZ timestamps, INTEGER 0/1 vs BOOLEAN nullability)
is caught by the same assertion set.
"""

from datetime import UTC, datetime, timedelta
from typing import cast

import aiosqlite
import pytest

from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.persistence.approval_protocol import ApprovalRepository
from synthorg.persistence.postgres.approval_repo import (
    PostgresApprovalRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.approval_repo import (
    SQLiteApprovalRepository,
)

pytestmark = pytest.mark.integration


def _approval_repo(backend: PersistenceBackend) -> ApprovalRepository:
    """Return a concrete ``ApprovalRepository`` bound to *backend*.

    The protocol does not surface on ``PersistenceBackend``; callers
    construct the repo directly today (via ``ApprovalStore``).  This
    helper mirrors that wiring under both backend variants.
    """
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteApprovalRepository(cast("aiosqlite.Connection", handle))
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresApprovalRepository(cast("AsyncConnectionPool", handle))
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _make_item(  # noqa: PLR0913
    *,
    approval_id: str = "approval-001",
    status: ApprovalStatus = ApprovalStatus.PENDING,
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.HIGH,
    action_type: str = "deploy:production",
    task_id: str | None = None,
    metadata: dict[str, str] | None = None,
) -> ApprovalItem:
    """Build an ``ApprovalItem`` with sensible defaults."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    if status in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
        decided_at = now
        decided_by = "operator-a"
        if status == ApprovalStatus.REJECTED:
            decision_reason = "Not authorised"
    return ApprovalItem(
        id=approval_id,
        action_type=action_type,
        title="Approve prod deploy",
        description="Rolls service v2 to prod.",
        requested_by="agent-eng-001",
        risk_level=risk_level,
        status=status,
        created_at=now,
        expires_at=now + timedelta(days=7),
        task_id=task_id,
        metadata=metadata or {},
        decided_at=decided_at,
        decided_by=decided_by,
        decision_reason=decision_reason,
    )


class TestApprovalRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        repo = _approval_repo(backend)
        item = _make_item(metadata={"source_rule": "rule-A", "confidence": "0.93"})
        await repo.save(item)

        fetched = await repo.get(item.id)
        assert fetched is not None
        assert fetched.id == item.id
        assert fetched.status is ApprovalStatus.PENDING
        assert fetched.action_type == item.action_type
        assert fetched.metadata == item.metadata
        assert fetched.created_at.tzinfo is not None
        assert fetched.expires_at is not None
        assert fetched.expires_at.tzinfo is not None

    async def test_get_returns_none_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _approval_repo(backend)
        assert await repo.get("approval-missing") is None

    async def test_save_commits_visible_to_fresh_repo(
        self, backend: PersistenceBackend
    ) -> None:
        # Writes must persist after the save returns -- guards against
        # missing ``await conn.commit()`` (silent rollback on Postgres
        # pool return).
        first = _approval_repo(backend)
        item = _make_item(approval_id="approval-commit")
        await first.save(item)

        second = _approval_repo(backend)
        fetched = await second.get(item.id)
        assert fetched is not None
        assert fetched.id == item.id

    async def test_save_upsert_overwrites_status(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _approval_repo(backend)
        item = _make_item(approval_id="approval-upsert")
        await repo.save(item)

        updated = item.model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": datetime(2026, 2, 1, tzinfo=UTC),
                "decided_by": "operator-b",
            },
        )
        await repo.save(updated)

        fetched = await repo.get(item.id)
        assert fetched is not None
        assert fetched.status is ApprovalStatus.APPROVED
        assert fetched.decided_by == "operator-b"

    async def test_list_items_no_filters_returns_all(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _approval_repo(backend)
        await repo.save(_make_item(approval_id="a"))
        await repo.save(_make_item(approval_id="b"))

        rows = await repo.list_items()
        ids = {r.id for r in rows}
        assert {"a", "b"} <= ids

    async def test_list_items_filter_by_status(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _approval_repo(backend)
        pending = _make_item(approval_id="p", status=ApprovalStatus.PENDING)
        approved = _make_item(approval_id="a", status=ApprovalStatus.APPROVED)
        await repo.save(pending)
        await repo.save(approved)

        only_pending = await repo.list_items(status=ApprovalStatus.PENDING)
        ids = {r.id for r in only_pending}
        assert "p" in ids
        assert "a" not in ids

    async def test_list_items_filter_by_risk_level(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _approval_repo(backend)
        high = _make_item(approval_id="h", risk_level=ApprovalRiskLevel.HIGH)
        crit = _make_item(approval_id="c", risk_level=ApprovalRiskLevel.CRITICAL)
        await repo.save(high)
        await repo.save(crit)

        only_critical = await repo.list_items(risk_level=ApprovalRiskLevel.CRITICAL)
        ids = {r.id for r in only_critical}
        assert "c" in ids
        assert "h" not in ids

    async def test_list_items_filter_by_action_type(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _approval_repo(backend)
        await repo.save(
            _make_item(approval_id="hire", action_type="scaling:hire"),
        )
        await repo.save(
            _make_item(approval_id="deploy", action_type="deploy:production"),
        )

        hires = await repo.list_items(action_type="scaling:hire")
        ids = {r.id for r in hires}
        assert "hire" in ids
        assert "deploy" not in ids

    async def test_list_items_combined_filters(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _approval_repo(backend)
        match = _make_item(
            approval_id="match",
            status=ApprovalStatus.PENDING,
            risk_level=ApprovalRiskLevel.HIGH,
            action_type="deploy:production",
        )
        wrong_status = _make_item(
            approval_id="wrong-status",
            status=ApprovalStatus.APPROVED,
            risk_level=ApprovalRiskLevel.HIGH,
            action_type="deploy:production",
        )
        wrong_risk = _make_item(
            approval_id="wrong-risk",
            status=ApprovalStatus.PENDING,
            risk_level=ApprovalRiskLevel.LOW,
            action_type="deploy:production",
        )
        for item in (match, wrong_status, wrong_risk):
            await repo.save(item)

        rows = await repo.list_items(
            status=ApprovalStatus.PENDING,
            risk_level=ApprovalRiskLevel.HIGH,
            action_type="deploy:production",
        )
        ids = {r.id for r in rows}
        assert ids == {"match"}

    async def test_delete_returns_true_then_false(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _approval_repo(backend)
        item = _make_item(approval_id="approval-del")
        await repo.save(item)
        assert await repo.get(item.id) is not None

        assert await repo.delete(item.id) is True
        assert await repo.get(item.id) is None

        assert await repo.delete(item.id) is False

    async def test_metadata_round_trip_preserves_keys(
        self, backend: PersistenceBackend
    ) -> None:
        # ``metadata`` is JSONB on Postgres, TEXT on SQLite; the
        # round-trip must produce an equal dict either way.
        repo = _approval_repo(backend)
        meta = {
            "source_rule": "rule-A",
            "confidence": "0.93",
            "actor": "agent-eng-001",
        }
        await repo.save(
            _make_item(approval_id="approval-meta", metadata=meta),
        )

        fetched = await repo.get("approval-meta")
        assert fetched is not None
        assert fetched.metadata == meta

    async def test_protocol_runtime_check(self, backend: PersistenceBackend) -> None:
        repo = _approval_repo(backend)
        assert isinstance(repo, ApprovalRepository)
