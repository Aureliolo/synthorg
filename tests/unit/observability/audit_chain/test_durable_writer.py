"""Unit tests for :class:`DurableAuditChainWriter`.

Covers the durability-critical paths: a clean stop flushes every queued
entry, overflow drops without blocking, and hydrate pages by cursor and
verifies the rebuilt chain.
"""

from datetime import UTC, datetime

import pytest

from synthorg.observability.audit_chain.chain import ChainEntry, HashChain
from synthorg.observability.audit_chain.durable_writer import (
    DurableAuditChainWriter,
)
from synthorg.persistence.audit_chain_protocol import AuditChainFilterSpec

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC)


class _FakeAuditChainRepo:
    """In-memory ``AuditChainRepository`` recording appended entries."""

    def __init__(self) -> None:
        self.entries: list[ChainEntry] = []
        self.query_calls = 0

    async def append(self, entry: ChainEntry) -> None:
        self.entries.append(entry)

    async def query(
        self,
        filter_spec: AuditChainFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ChainEntry, ...]:
        self.query_calls += 1
        rows = sorted(self.entries, key=lambda e: e.position)
        if filter_spec.min_position is not None:
            rows = [e for e in rows if e.position >= filter_spec.min_position]
        return tuple(rows[offset : offset + limit])

    async def purge_before(self, threshold: datetime, /) -> int:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.timestamp >= threshold]
        return before - len(self.entries)

    async def get_tail(self) -> ChainEntry | None:
        return max(self.entries, key=lambda e: e.position) if self.entries else None


def _entries(count: int) -> tuple[ChainEntry, ...]:
    """Build ``count`` valid, correctly-linked chain entries."""
    chain = HashChain()
    for i in range(count):
        chain.append(f"event-{i}".encode(), f"sig-{i}".encode(), _NOW)
    return chain.entries


async def test_clean_stop_flushes_every_enqueued_entry() -> None:
    repo = _FakeAuditChainRepo()
    writer = DurableAuditChainWriter(repo)
    await writer.start()
    entries = _entries(5)
    for entry in entries:
        writer.enqueue(entry)
    await writer.stop()
    assert [e.position for e in repo.entries] == [e.position for e in entries]


async def test_enqueue_overflow_drops_without_blocking() -> None:
    repo = _FakeAuditChainRepo()
    # Tiny queue, drain not started: the third enqueue overflows.
    writer = DurableAuditChainWriter(repo, queue_maxsize=2)
    entries = _entries(3)
    for entry in entries:
        writer.enqueue(entry)  # must never block or raise
    # Nothing drained (no drain task), and the durable repo stays empty.
    assert repo.entries == []


async def test_hydrate_restores_chain_and_passes_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeAuditChainRepo()
    for entry in _entries(7):
        await repo.append(entry)
    # Shrink the hydrate page size below the seeded count so the cursor
    # pagination genuinely spans multiple pages (ceil(7/3) = 3 fetches)
    # instead of degrading to a single query that would still pass.
    monkeypatch.setattr(
        "synthorg.observability.audit_chain.durable_writer._HYDRATE_PAGE_SIZE",
        3,
    )
    writer = DurableAuditChainWriter(repo)
    chain = HashChain()
    await writer.hydrate(chain)
    assert [e.position for e in chain.entries] == list(range(7))
    assert chain.verify_integrity()
    assert repo.query_calls == 3


async def test_hydrate_flags_a_broken_chain() -> None:
    repo = _FakeAuditChainRepo()
    good = _entries(2)
    await repo.append(good[0])
    # A tampered second entry whose previous_hash does not link.
    tampered = good[1].model_copy(update={"previous_hash": "tampered"})
    await repo.append(tampered)
    writer = DurableAuditChainWriter(repo)
    chain = HashChain()
    await writer.hydrate(chain)
    # The chain is still loaded (appends can continue) but does not verify.
    assert len(chain.entries) == 2
    assert not chain.verify_integrity()
