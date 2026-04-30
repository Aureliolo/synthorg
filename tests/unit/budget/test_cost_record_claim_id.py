"""Tests for CostRecord.claim_id idempotency field and tracker dedup.

Issue #1682: every CostRecord carries a deterministic ``claim_id``
generated at construction. ``CostTracker.record`` keeps a bounded
LRU set of seen ``claim_id`` values; a second submission of the same
key is a no-op and emits a single ``BUDGET_RECORD_DEDUPED`` info log.
This protects future JetStream consumers (and the in-process tracker
itself) from double-billing on retry.
"""

import asyncio
from collections import OrderedDict

import pytest
import structlog.testing
from pydantic import ValidationError

from synthorg.budget.tracker import CostTracker
from synthorg.observability.events.budget import (
    BUDGET_RECORD_ADDED,
    BUDGET_RECORD_DEDUPED,
)

from .conftest import make_cost_record


@pytest.mark.unit
class TestClaimIdField:
    """``CostRecord.claim_id`` field shape and defaults."""

    def test_default_claim_id_is_unique_per_instance(self) -> None:
        """Two records constructed without an explicit claim_id differ."""
        a = make_cost_record()
        b = make_cost_record()
        assert a.claim_id != b.claim_id
        assert a.claim_id  # non-empty
        assert b.claim_id

    def test_default_claim_id_looks_like_uuid4(self) -> None:
        """Default factory yields a UUID4 string (8-4-4-4-12 hex)."""
        rec = make_cost_record()
        # 36 chars: 32 hex + 4 hyphens, lowercase.
        expected_length = 36
        assert len(rec.claim_id) == expected_length
        parts = rec.claim_id.split("-")
        assert [len(p) for p in parts] == [8, 4, 4, 4, 12]
        for part in parts:
            int(part, 16)  # raises ValueError if not hex

    def test_explicit_claim_id_is_preserved(self) -> None:
        """Caller-supplied claim_id is honoured (idempotent reconstruction)."""
        explicit = "deterministic-key-001"
        rec = make_cost_record()
        # CostRecord is frozen; build a fresh one with the explicit key
        # via model_copy so we exercise the field through Pydantic.
        copied = rec.model_copy(update={"claim_id": explicit})
        assert copied.claim_id == explicit

    def test_blank_claim_id_rejected(self) -> None:
        """Empty / whitespace claim_id is rejected by NotBlankStr.

        ``model_copy(update=...)`` bypasses validation by design;
        construct fresh records via ``model_validate`` so the
        ``NotBlankStr`` validator runs.
        """
        rec = make_cost_record()
        base_payload = rec.model_dump()
        with pytest.raises(ValidationError):
            type(rec).model_validate({**base_payload, "claim_id": ""})
        with pytest.raises(ValidationError):
            type(rec).model_validate({**base_payload, "claim_id": "   "})

    def test_claim_id_is_immutable(self) -> None:
        """Frozen Pydantic model: direct assignment fails."""
        rec = make_cost_record()
        with pytest.raises(ValidationError):
            rec.claim_id = "should-not-mutate"  # type: ignore[misc]


@pytest.mark.unit
class TestTrackerLruDedup:
    """``CostTracker.record`` dedupes on ``cost_record.claim_id``."""

    async def test_repeat_claim_id_is_no_op(
        self,
        cost_tracker: CostTracker,
    ) -> None:
        """Second record with same claim_id does not append a second entry."""
        rec = make_cost_record(cost=0.10)
        await cost_tracker.record(rec)
        await cost_tracker.record(rec)

        assert await cost_tracker.get_record_count() == 1
        assert await cost_tracker.get_total_cost() == 0.10

    async def test_repeat_claim_id_emits_deduped_log(
        self,
        cost_tracker: CostTracker,
    ) -> None:
        """The duplicate submission emits BUDGET_RECORD_DEDUPED at INFO."""
        rec = make_cost_record(agent_id="alice", cost=0.05)
        await cost_tracker.record(rec)
        with structlog.testing.capture_logs() as logs:
            await cost_tracker.record(rec)

        deduped = [
            entry for entry in logs if entry.get("event") == BUDGET_RECORD_DEDUPED
        ]
        assert len(deduped) == 1
        assert deduped[0]["claim_id"] == rec.claim_id
        assert deduped[0]["agent_id"] == "alice"
        assert deduped[0]["cost"] == 0.05

        # No second BUDGET_RECORD_ADDED on the duplicate.
        added = [entry for entry in logs if entry.get("event") == BUDGET_RECORD_ADDED]
        assert added == []

    async def test_distinct_claim_ids_both_appended(
        self,
        cost_tracker: CostTracker,
    ) -> None:
        """Records with distinct claim_ids each produce one entry."""
        await cost_tracker.record(make_cost_record(cost=0.10))
        await cost_tracker.record(make_cost_record(cost=0.20))

        assert await cost_tracker.get_record_count() == 2

    async def test_lru_evicts_oldest_at_capacity(self) -> None:
        """When the LRU cap is exceeded, oldest claim_ids are evicted.

        Once a claim_id is evicted, a re-submission of that record is
        no longer recognised as a duplicate. This is the documented
        behaviour: the LRU is best-effort dedup, bounded so the
        tracker cannot grow forever.
        """
        tracker = CostTracker(claim_lru_capacity=3)
        first = make_cost_record(cost=0.01)
        records = [
            first,
            make_cost_record(cost=0.02),
            make_cost_record(cost=0.03),
            make_cost_record(cost=0.04),  # this evicts `first` from the LRU
        ]
        for rec in records:
            await tracker.record(rec)

        # All four were unique; all four appended.
        assert await tracker.get_record_count() == 4

        # Re-submit the first record. Its claim_id has been evicted,
        # so the tracker treats it as fresh.
        await tracker.record(first)
        assert await tracker.get_record_count() == 5

    async def test_lru_invalid_capacity_rejected(self) -> None:
        """Capacity < 1 is a constructor error."""
        with pytest.raises(ValueError, match="claim_lru_capacity"):
            CostTracker(claim_lru_capacity=0)

    async def test_lru_state_is_ordereddict(
        self,
        cost_tracker: CostTracker,
    ) -> None:
        """Dedup state is an OrderedDict (LRU semantics)."""
        # Private attribute reach: this is a structural invariant the
        # implementation contract documents.
        assert isinstance(cost_tracker._seen_claims, OrderedDict)

    async def test_concurrent_same_claim_id_dedupes(
        self,
        cost_tracker: CostTracker,
    ) -> None:
        """Concurrent submissions of the same claim_id collapse to one record.

        Pre-PR review finding (#1682, item #5): the LRU fast-path is
        guarded by ``self._lock``. Two concurrent ``record(rec)`` calls
        with the same ``claim_id`` must serialize through the lock and
        leave exactly one entry in ``_records``. A future refactor that
        moved the membership check outside the lock would silently
        double-bill in production; this test catches that regression.
        """
        rec = make_cost_record(cost=0.07)
        await asyncio.gather(
            cost_tracker.record(rec),
            cost_tracker.record(rec),
            cost_tracker.record(rec),
        )
        assert await cost_tracker.get_record_count() == 1
        assert await cost_tracker.get_total_cost() == 0.07

    async def test_lru_touch_on_resubmit_moves_to_tail(self) -> None:
        """Resubmitting an existing claim_id moves it to the LRU tail.

        With capacity=2 we record A then B, then resubmit A (which
        must be detected as a duplicate AND moved to the tail), then
        record C. Eviction is FIFO from the head; since A was touched
        and moved to the tail, the head when C arrives is B. C should
        evict B, not A. Re-submitting A afterwards must still be a
        no-op (A is still in the LRU); re-submitting B must be treated
        as fresh (B was evicted).
        """
        tracker = CostTracker(claim_lru_capacity=2)
        rec_a = make_cost_record(cost=0.01)
        rec_b = make_cost_record(cost=0.02)
        rec_c = make_cost_record(cost=0.04)

        await tracker.record(rec_a)
        await tracker.record(rec_b)
        # Touch A: must move to tail.
        await tracker.record(rec_a)
        assert await tracker.get_record_count() == 2
        # Push C: capacity exceeded; the head (now B) must be evicted.
        await tracker.record(rec_c)
        assert await tracker.get_record_count() == 3

        # A is still cached -> resubmission stays a no-op.
        await tracker.record(rec_a)
        assert await tracker.get_record_count() == 3

        # B was evicted -> resubmission is treated as fresh.
        await tracker.record(rec_b)
        assert await tracker.get_record_count() == 4
