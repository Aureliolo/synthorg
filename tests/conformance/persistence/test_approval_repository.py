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
from synthorg.core.types import NotBlankStr
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
        return SQLiteApprovalRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
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

    async def test_save_many_round_trips_batch(
        self,
        backend: PersistenceBackend,
    ) -> None:
        # save_many writes every item under one transaction.  All rows
        # must be visible to a fresh repo read after the call returns.
        repo = _approval_repo(backend)
        items = tuple(_make_item(approval_id=f"approval-batch-{i}") for i in range(5))
        await repo.save_many(items)

        fresh = _approval_repo(backend)
        for original in items:
            fetched = await fresh.get(original.id)
            assert fetched is not None, original.id
            assert fetched.id == original.id

    async def test_save_many_empty_input_is_noop(
        self,
        backend: PersistenceBackend,
    ) -> None:
        repo = _approval_repo(backend)
        # Empty input must not open a transaction or raise.
        await repo.save_many(())
        # Confirm no rows were written: a fresh repo on the same
        # connection sees an empty list. Without this post-condition
        # the test would pass even if save_many silently opened and
        # committed an empty transaction.
        fresh = _approval_repo(backend)
        assert await fresh.list_items() == ()

    async def test_save_many_upserts_existing_rows(
        self,
        backend: PersistenceBackend,
    ) -> None:
        # save_many must obey the same upsert semantics as save() so a
        # batched expiry loop can transition PENDING to EXPIRED on
        # already-persisted items in one call. Use a multi-item batch
        # (a peer fresh insert + the upsert under test) so the repo
        # actually exercises its executemany / batched-upsert path
        # rather than delegating to the single-item ``save()``
        # fast-path that both backends short-circuit on len(items)==1.
        repo = _approval_repo(backend)
        original = _make_item(approval_id="approval-batch-upsert")
        await repo.save(original)

        updated = original.model_copy(update={"status": ApprovalStatus.EXPIRED})
        peer = _make_item(approval_id="approval-batch-upsert-peer")
        await repo.save_many((updated, peer))

        fetched = await repo.get(original.id)
        assert fetched is not None
        assert fetched.status is ApprovalStatus.EXPIRED
        peer_fetched = await repo.get(peer.id)
        assert peer_fetched is not None
        assert peer_fetched.status is ApprovalStatus.PENDING

    async def test_save_many_duplicate_ids_within_batch_settle_to_last(
        self,
        backend: PersistenceBackend,
    ) -> None:
        # The protocol contract is upsert per id. When the same id
        # appears twice in a batch the repository must converge on the
        # last value rather than open a half-applied state where a
        # concurrent reader could observe the intermediate version.
        repo = _approval_repo(backend)
        first = _make_item(
            approval_id="approval-batch-dup",
            status=ApprovalStatus.PENDING,
        )
        second = first.model_copy(update={"status": ApprovalStatus.EXPIRED})
        await repo.save_many((first, second))

        fetched = await repo.get(first.id)
        assert fetched is not None
        assert fetched.status is ApprovalStatus.EXPIRED

    async def test_expire_if_pending_flips_pending_rows_only(
        self,
        backend: PersistenceBackend,
    ) -> None:
        # Compare-and-set contract: rows still PENDING transition to
        # EXPIRED; rows already in a terminal status are silently
        # skipped. Returned ids reflect what actually changed.
        repo = _approval_repo(backend)
        pending = _make_item(
            approval_id="approval-expire-pending",
            status=ApprovalStatus.PENDING,
        )
        approved = _make_item(
            approval_id="approval-expire-approved",
            status=ApprovalStatus.APPROVED,
        )
        rejected = _make_item(
            approval_id="approval-expire-rejected",
            status=ApprovalStatus.REJECTED,
        )
        await repo.save_many((pending, approved, rejected))

        updated = await repo.expire_if_pending(
            (pending.id, approved.id, rejected.id),
        )
        assert set(updated) == {pending.id}
        assert (await repo.get(pending.id)).status is ApprovalStatus.EXPIRED  # type: ignore[union-attr]
        assert (await repo.get(approved.id)).status is ApprovalStatus.APPROVED  # type: ignore[union-attr]
        assert (await repo.get(rejected.id)).status is ApprovalStatus.REJECTED  # type: ignore[union-attr]

    async def test_expire_if_pending_empty_input_is_noop(
        self,
        backend: PersistenceBackend,
    ) -> None:
        repo = _approval_repo(backend)
        result = await repo.expire_if_pending(())
        assert result == ()

    async def test_expire_if_pending_unknown_ids_returned_empty(
        self,
        backend: PersistenceBackend,
    ) -> None:
        # Ids that don't exist in the table are silently skipped, same
        # as a row that's already terminal -- the compare-and-set
        # WHERE clause matches no row, so no row is returned.
        repo = _approval_repo(backend)
        updated = await repo.expire_if_pending(
            (NotBlankStr("approval-expire-missing"),),
        )
        assert updated == ()

    async def test_get_many_round_trips_batch(
        self,
        backend: PersistenceBackend,
    ) -> None:
        repo = _approval_repo(backend)
        ids = ("approval-batch-001", "approval-batch-002", "approval-batch-003")
        for approval_id in ids:
            await repo.save(_make_item(approval_id=approval_id))

        items = await repo.get_many(tuple(NotBlankStr(i) for i in ids))
        assert {item.id for item in items} == set(ids)
        # Order is unspecified; the protocol doesn't promise it.
        assert len(items) == len(ids)

    async def test_get_many_empty_input_is_noop(
        self,
        backend: PersistenceBackend,
    ) -> None:
        repo = _approval_repo(backend)
        # Empty input must short-circuit without issuing SQL.
        result = await repo.get_many(())
        assert result == ()

    async def test_get_many_partial_miss(
        self,
        backend: PersistenceBackend,
    ) -> None:
        repo = _approval_repo(backend)
        await repo.save(_make_item(approval_id="approval-partial-001"))
        items = await repo.get_many(
            (
                NotBlankStr("approval-partial-001"),
                NotBlankStr("approval-partial-missing"),
            ),
        )
        assert len(items) == 1
        assert items[0].id == "approval-partial-001"

    async def test_get_many_duplicate_ids_dedupe(
        self,
        backend: PersistenceBackend,
    ) -> None:
        # Both backends use ``WHERE id IN (...)`` / ``id = ANY(%s)`` --
        # SQL deduplicates the result naturally, so duplicate ids in
        # the input return one row each. Pin this contract so a
        # future implementation cannot silently emit duplicates.
        repo = _approval_repo(backend)
        await repo.save(_make_item(approval_id="approval-dup-001"))
        items = await repo.get_many(
            (
                NotBlankStr("approval-dup-001"),
                NotBlankStr("approval-dup-001"),
                NotBlankStr("approval-dup-001"),
            ),
        )
        assert len(items) == 1
        assert items[0].id == "approval-dup-001"
