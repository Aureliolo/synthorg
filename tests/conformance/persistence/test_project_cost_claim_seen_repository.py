"""Conformance tests for ``ProjectCostClaimSeenRepository``.

Runs once against SQLite and once against a real Postgres container via
the parametrised ``backend`` fixture so the two implementations stay in
lockstep on the durable cost-claim dedup contract (audit 133).
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _now() -> datetime:
    return datetime.now(UTC)


class TestMarkSeen:
    async def test_first_mark_returns_true(
        self,
        backend: PersistenceBackend,
    ) -> None:
        inserted = await backend.project_cost_claim_seen.mark_seen(
            claim_id=NotBlankStr("claim-first"),
            project_id=NotBlankStr("proj-1"),
            now=_now(),
            ttl_seconds=60.0,
        )
        assert inserted is True

    async def test_second_mark_returns_false(
        self,
        backend: PersistenceBackend,
    ) -> None:
        claim = NotBlankStr("claim-dup")
        first = await backend.project_cost_claim_seen.mark_seen(
            claim_id=claim,
            project_id=NotBlankStr("proj-1"),
            now=_now(),
            ttl_seconds=60.0,
        )
        second = await backend.project_cost_claim_seen.mark_seen(
            claim_id=claim,
            project_id=NotBlankStr("proj-1"),
            now=_now(),
            ttl_seconds=60.0,
        )
        assert first is True
        assert second is False

    async def test_distinct_claims_both_insert(
        self,
        backend: PersistenceBackend,
    ) -> None:
        first = await backend.project_cost_claim_seen.mark_seen(
            claim_id=NotBlankStr("claim-a"),
            project_id=NotBlankStr("proj-1"),
            now=_now(),
            ttl_seconds=60.0,
        )
        second = await backend.project_cost_claim_seen.mark_seen(
            claim_id=NotBlankStr("claim-b"),
            project_id=NotBlankStr("proj-2"),
            now=_now(),
            ttl_seconds=60.0,
        )
        assert first is True
        assert second is True


class TestHasSeen:
    async def test_returns_false_for_unmarked_claim(
        self,
        backend: PersistenceBackend,
    ) -> None:
        seen = await backend.project_cost_claim_seen.has_seen(
            claim_id=NotBlankStr("claim-never-marked"),
        )
        assert seen is False

    async def test_returns_true_after_mark_seen(
        self,
        backend: PersistenceBackend,
    ) -> None:
        claim = NotBlankStr("claim-billed")
        before = await backend.project_cost_claim_seen.has_seen(claim_id=claim)
        assert before is False

        await backend.project_cost_claim_seen.mark_seen(
            claim_id=claim,
            project_id=NotBlankStr("proj-1"),
            now=_now(),
            ttl_seconds=60.0,
        )

        after = await backend.project_cost_claim_seen.has_seen(claim_id=claim)
        assert after is True


class TestPruneExpired:
    async def test_prune_removes_expired_rows(
        self,
        backend: PersistenceBackend,
    ) -> None:
        seeded_at = _now() - timedelta(seconds=120)
        await backend.project_cost_claim_seen.mark_seen(
            claim_id=NotBlankStr("claim-old"),
            project_id=NotBlankStr("proj-1"),
            now=seeded_at,
            ttl_seconds=1.0,
        )
        removed = await backend.project_cost_claim_seen.prune_expired(_now())
        assert removed >= 1
        # Re-marking should now succeed because the row was pruned.
        fresh = await backend.project_cost_claim_seen.mark_seen(
            claim_id=NotBlankStr("claim-old"),
            project_id=NotBlankStr("proj-1"),
            now=_now(),
            ttl_seconds=60.0,
        )
        assert fresh is True

    async def test_prune_leaves_live_rows(
        self,
        backend: PersistenceBackend,
    ) -> None:
        await backend.project_cost_claim_seen.mark_seen(
            claim_id=NotBlankStr("claim-live"),
            project_id=NotBlankStr("proj-1"),
            now=_now(),
            ttl_seconds=600.0,
        )
        removed = await backend.project_cost_claim_seen.prune_expired(_now())
        assert removed == 0
        duplicate = await backend.project_cost_claim_seen.mark_seen(
            claim_id=NotBlankStr("claim-live"),
            project_id=NotBlankStr("proj-1"),
            now=_now(),
            ttl_seconds=600.0,
        )
        assert duplicate is False
