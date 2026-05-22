"""Unit tests for the ring-buffer capability-gap store."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.toolsmith.gap_store import RingBufferCapabilityGapStore
from synthorg.meta.toolsmith.protocol import CapabilityGapStore

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)


def _store(capacity: int = 64) -> RingBufferCapabilityGapStore:
    return RingBufferCapabilityGapStore(max_observations=capacity)


class TestRingBufferCapabilityGapStore:
    def test_satisfies_protocol(self) -> None:
        assert isinstance(_store(), CapabilityGapStore)

    def test_rejects_zero_capacity(self) -> None:
        with pytest.raises(ValueError, match="max_observations"):
            RingBufferCapabilityGapStore(max_observations=0)

    async def test_records_and_counts(self) -> None:
        store = _store()
        await store.record_gap(NotBlankStr("textkit:slugify"), occurred_at=_NOW)
        await store.record_gap(NotBlankStr("textkit:slugify"), occurred_at=_NOW)
        assert await store.count() == 2

    async def test_recurring_requires_threshold(self) -> None:
        store = _store()
        for _ in range(2):
            await store.record_gap(NotBlankStr("textkit:slugify"), occurred_at=_NOW)
        # threshold 3 not met
        assert (
            await store.recurring(threshold=3, window=timedelta(hours=24), now=_NOW)
            == ()
        )

    async def test_recurring_fires_on_threshold(self) -> None:
        store = _store()
        for i in range(3):
            await store.record_gap(
                NotBlankStr("textkit:slugify"),
                occurred_at=_NOW + timedelta(minutes=i),
            )
        gaps = await store.recurring(
            threshold=3, window=timedelta(hours=24), now=_NOW + timedelta(minutes=3)
        )
        assert len(gaps) == 1
        assert gaps[0].signature == "textkit:slugify"
        assert gaps[0].occurrences == 3
        assert gaps[0].first_seen == _NOW
        assert gaps[0].last_seen == _NOW + timedelta(minutes=2)

    async def test_recurring_excludes_out_of_window(self) -> None:
        store = _store()
        # Two recent, one old (outside the 1h window)
        await store.record_gap(
            NotBlankStr("textkit:slugify"), occurred_at=_NOW - timedelta(hours=5)
        )
        await store.record_gap(NotBlankStr("textkit:slugify"), occurred_at=_NOW)
        await store.record_gap(NotBlankStr("textkit:slugify"), occurred_at=_NOW)
        gaps = await store.recurring(threshold=3, window=timedelta(hours=1), now=_NOW)
        assert gaps == ()

    async def test_recurring_ranks_by_frequency(self) -> None:
        store = _store()
        for _ in range(2):
            await store.record_gap(NotBlankStr("a:one"), occurred_at=_NOW)
        for _ in range(4):
            await store.record_gap(NotBlankStr("b:two"), occurred_at=_NOW)
        gaps = await store.recurring(threshold=2, window=timedelta(hours=1), now=_NOW)
        assert [g.signature for g in gaps] == ["b:two", "a:one"]

    async def test_naive_timestamp_rejected_in_record_is_swallowed(self) -> None:
        store = _store()
        # Naive timestamp -> recording is best-effort, swallowed, nothing stored.
        await store.record_gap(
            NotBlankStr("textkit:slugify"),
            occurred_at=datetime(2026, 5, 21, 12, 0),  # noqa: DTZ001
        )
        assert await store.count() == 0

    async def test_clear(self) -> None:
        store = _store()
        await store.record_gap(NotBlankStr("a:one"), occurred_at=_NOW)
        await store.clear()
        assert await store.count() == 0

    async def test_eviction_at_capacity(self) -> None:
        store = _store(capacity=2)
        for i in range(4):
            await store.record_gap(
                NotBlankStr("a:one"), occurred_at=_NOW + timedelta(minutes=i)
            )
        assert await store.count() == 2
