"""Tests for DelegationRecordStore."""

import threading
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
import structlog

from synthorg.communication.delegation.models import DelegationRecord
from synthorg.communication.delegation.record_store import (
    DelegationRecordStore,
)
from synthorg.observability.events.delegation import DELEGATION_RECORD_EVICTED

_NOW = datetime(2026, 3, 24, 12, 0, 0, tzinfo=UTC)


def _make_record(  # noqa: PLR0913
    *,
    delegation_id: str = "del-001",
    delegator_id: str = "agent-manager",
    delegatee_id: str = "agent-worker",
    original_task_id: str = "task-parent",
    delegated_task_id: str = "del-abc123",
    timestamp: datetime = _NOW,
) -> DelegationRecord:
    return DelegationRecord(
        delegation_id=delegation_id,
        delegator_id=delegator_id,
        delegatee_id=delegatee_id,
        original_task_id=original_task_id,
        delegated_task_id=delegated_task_id,
        timestamp=timestamp,
    )


@pytest.mark.unit
class TestDelegationRecordStore:
    async def test_empty_store(self) -> None:
        store = DelegationRecordStore()
        records = await store.get_all_records()
        assert records == ()

    async def test_append_and_retrieve(self) -> None:
        store = DelegationRecordStore()
        record = _make_record()
        store.append(record)

        records = await store.get_all_records()
        assert len(records) == 1
        assert records[0].delegation_id == "del-001"

    async def test_filter_as_delegator(self) -> None:
        store = DelegationRecordStore()
        store.append(
            _make_record(delegator_id="alice", delegatee_id="bob"),
        )
        store.append(
            _make_record(
                delegation_id="del-002",
                delegator_id="bob",
                delegatee_id="charlie",
            ),
        )

        records = await store.get_records_as_delegator("alice")
        assert len(records) == 1
        assert records[0].delegator_id == "alice"

    async def test_filter_as_delegatee(self) -> None:
        store = DelegationRecordStore()
        store.append(
            _make_record(delegator_id="alice", delegatee_id="bob"),
        )
        store.append(
            _make_record(
                delegation_id="del-002",
                delegator_id="bob",
                delegatee_id="charlie",
            ),
        )

        records = await store.get_records_as_delegatee("bob")
        assert len(records) == 1
        assert records[0].delegatee_id == "bob"

    async def test_time_range_filtering(self) -> None:
        store = DelegationRecordStore()
        store.append(
            _make_record(
                delegation_id="del-old",
                timestamp=_NOW - timedelta(hours=3),
            ),
        )
        store.append(
            _make_record(
                delegation_id="del-mid",
                timestamp=_NOW - timedelta(hours=1),
            ),
        )
        store.append(
            _make_record(delegation_id="del-new", timestamp=_NOW),
        )

        records = await store.get_all_records(
            start=_NOW - timedelta(hours=2),
            end=_NOW,
        )
        assert len(records) == 1
        assert records[0].delegation_id == "del-mid"

    async def test_get_all_records_returns_all(self) -> None:
        store = DelegationRecordStore()
        store.append(_make_record(delegation_id="del-001"))
        store.append(_make_record(delegation_id="del-002"))

        records = await store.get_all_records()
        assert len(records) == 2

    async def test_invalid_time_range(self) -> None:
        store = DelegationRecordStore()
        with pytest.raises(ValueError, match=r"start.*must be before"):
            await store.get_all_records(
                start=_NOW,
                end=_NOW - timedelta(hours=1),
            )

    async def test_equal_start_end_raises(self) -> None:
        store = DelegationRecordStore()
        with pytest.raises(ValueError, match=r"start.*must be before"):
            await store.get_all_records(start=_NOW, end=_NOW)

    async def test_returns_immutable_tuple(self) -> None:
        store = DelegationRecordStore()
        store.append(_make_record())
        records = await store.get_all_records()
        assert isinstance(records, tuple)

    async def test_delegator_time_range_filtering(self) -> None:
        """Time range filtering works on get_records_as_delegator."""
        store = DelegationRecordStore()
        store.append(
            _make_record(
                delegation_id="del-old",
                delegator_id="alice",
                timestamp=_NOW - timedelta(hours=3),
            ),
        )
        store.append(
            _make_record(
                delegation_id="del-mid",
                delegator_id="alice",
                timestamp=_NOW - timedelta(hours=1),
            ),
        )

        # Half-open interval: start <= ts < end
        records = await store.get_records_as_delegator(
            "alice",
            start=_NOW - timedelta(hours=2),
            end=_NOW,
        )
        assert len(records) == 1
        assert records[0].delegation_id == "del-mid"

    async def test_delegatee_time_range_filtering(self) -> None:
        """Time range filtering works on get_records_as_delegatee."""
        store = DelegationRecordStore()
        store.append(
            _make_record(
                delegation_id="del-old",
                delegatee_id="bob",
                timestamp=_NOW - timedelta(hours=3),
            ),
        )
        store.append(
            _make_record(
                delegation_id="del-mid",
                delegatee_id="bob",
                timestamp=_NOW - timedelta(hours=1),
            ),
        )

        # Half-open interval: start <= ts < end
        records = await store.get_records_as_delegatee(
            "bob",
            start=_NOW - timedelta(hours=2),
            end=_NOW,
        )
        assert len(records) == 1
        assert records[0].delegation_id == "del-mid"


# ── Eviction ────────────────────────────────────────────────


@pytest.mark.unit
class TestDelegationRecordStoreEviction:
    """FIFO eviction when record count exceeds max_records."""

    async def test_eviction_when_max_exceeded(self) -> None:
        store = DelegationRecordStore(max_records=3)
        for i in range(5):
            store.append(
                _make_record(
                    delegation_id=f"del-{i:03d}",
                    timestamp=_NOW + timedelta(seconds=i),
                ),
            )
        records = await store.get_all_records()
        assert len(records) == 3
        # Oldest two evicted
        assert records[0].delegation_id == "del-002"
        assert records[2].delegation_id == "del-004"

    async def test_no_eviction_below_max(self) -> None:
        store = DelegationRecordStore(max_records=10)
        for i in range(5):
            store.append(
                _make_record(delegation_id=f"del-{i:03d}"),
            )
        records = await store.get_all_records()
        assert len(records) == 5

    @pytest.mark.parametrize("value", [0, -1], ids=["zero", "negative"])
    def test_max_records_invalid_rejected(self, value: int) -> None:
        with pytest.raises(ValueError, match="max_records must be >= 1"):
            DelegationRecordStore(max_records=value)

    async def test_no_eviction_at_exact_max(self) -> None:
        store = DelegationRecordStore(max_records=3)
        for i in range(3):
            store.append(
                _make_record(delegation_id=f"del-{i:03d}"),
            )
        records = await store.get_all_records()
        assert len(records) == 3
        assert records[0].delegation_id == "del-000"
        assert records[2].delegation_id == "del-002"

    async def test_max_records_one_keeps_only_last(self) -> None:
        store = DelegationRecordStore(max_records=1)
        store.append(_make_record(delegation_id="del-first"))
        store.append(_make_record(delegation_id="del-last"))
        records = await store.get_all_records()
        assert len(records) == 1
        assert records[0].delegation_id == "del-last"


# ── Concurrency: warning guarded by threading.Lock ────────────


def _count_eviction_warnings(cap: Sequence[Mapping[str, object]]) -> int:
    return sum(1 for e in cap if e.get("event") == DELEGATION_RECORD_EVICTED)


@pytest.mark.unit
class TestEvictionWarningConcurrency:
    """Check-then-set on _eviction_warned must be serialized."""

    def test_single_warning_under_thread_flood(self) -> None:
        """Many threads filling past maxlen emit exactly one warning."""
        store = DelegationRecordStore(max_records=5)
        record_count = 200
        barrier = threading.Barrier(record_count)

        def worker(i: int) -> None:
            barrier.wait()
            store.append(_make_record(delegation_id=f"del-{i:04d}"))

        with (
            structlog.testing.capture_logs() as cap,
            ThreadPoolExecutor(max_workers=record_count) as executor,
        ):
            list(executor.map(worker, range(record_count)))

        assert _count_eviction_warnings(cap) == 1

    def test_clear_then_refill_re_emits_warning(self) -> None:
        """clear() resets the flag so a subsequent fill warns again."""
        store = DelegationRecordStore(max_records=3)

        with structlog.testing.capture_logs() as cap:
            for i in range(5):
                store.append(_make_record(delegation_id=f"a-{i}"))
            store.clear()
            for i in range(5):
                store.append(_make_record(delegation_id=f"b-{i}"))

        assert _count_eviction_warnings(cap) == 2

    def test_interleaved_clear_and_record_emits_exactly_one_warning_per_cycle(
        self,
    ) -> None:
        """Each fill cycle that overflows emits its warning exactly once.

        Cycles are serialised by a barrier so each executes fully before
        the next starts -- this is what "exactly once per cycle" actually
        means.  Threads within a cycle race through 12 ``append``
        calls concurrently past ``max_records=3`` and the closing
        ``clear()``; because the check/set on ``_eviction_warned`` and
        the buffer mutations are both held under ``_warning_lock``, the
        cycle emits exactly one warning regardless of thread scheduling.
        """
        store = DelegationRecordStore(max_records=3)
        cycles = 30
        fills_per_cycle = 12
        threads_per_cycle = 8

        def fill_thread(start: threading.Barrier, done: threading.Barrier) -> None:
            start.wait()
            for i in range(fills_per_cycle):
                store.append(_make_record(delegation_id=f"d-{i:03d}"))
            done.wait()

        with structlog.testing.capture_logs() as cap:
            for _cycle in range(cycles):
                start = threading.Barrier(threads_per_cycle)
                done = threading.Barrier(threads_per_cycle + 1)
                with ThreadPoolExecutor(max_workers=threads_per_cycle) as executor:
                    futures = [
                        executor.submit(fill_thread, start, done)
                        for _ in range(threads_per_cycle)
                    ]
                    done.wait()
                    for future in futures:
                        future.result()
                store.clear()

        # With per-cycle serialisation + the lock protecting both the
        # flag and the buffer, exactly one warning fires per cycle.
        assert _count_eviction_warnings(cap) == cycles
