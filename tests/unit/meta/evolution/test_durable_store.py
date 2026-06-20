"""Unit tests for the durable evolution-outcome store + read service.

Drives :class:`DurableEvolutionOutcomeStore` and
:class:`EvolutionReadService` over an in-memory fake repository so the
write-through, rehydrate, hot-read, and read-view roll-ups are covered
without a real backend.
"""

from datetime import UTC, datetime, timedelta
from typing import override

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.evolution.durable_store import DurableEvolutionOutcomeStore
from synthorg.meta.evolution.outcome_models import EvolutionOutcomeRecord
from synthorg.meta.evolution.read_service import EvolutionReadService
from synthorg.persistence.evolution_outcome_protocol import (
    EvolutionOutcomeFilterSpec,
)
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


class _InMemoryOutcomeRepo:
    """List-backed fake satisfying ``EvolutionOutcomeRepository``."""

    def __init__(self) -> None:
        self.records: list[EvolutionOutcomeRecord] = []

    async def append(self, event: EvolutionOutcomeRecord) -> None:
        self.records.append(event)

    async def query(
        self,
        filter_spec: EvolutionOutcomeFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[EvolutionOutcomeRecord, ...]:
        rows = [r for r in self.records if _matches(r, filter_spec)]
        rows.sort(key=lambda r: r.recorded_at, reverse=True)
        return tuple(rows[offset : offset + limit])

    async def purge_before(self, threshold: datetime) -> int:
        keep = [r for r in self.records if r.recorded_at >= threshold]
        removed = len(self.records) - len(keep)
        self.records = keep
        return removed

    async def axis_counts(
        self, *, since: datetime, until: datetime
    ) -> tuple[tuple[NotBlankStr, int], ...]:
        counts: dict[str, int] = {}
        for r in self.records:
            if since <= r.recorded_at < until:
                counts[r.axis] = counts.get(r.axis, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return tuple((NotBlankStr(axis), n) for axis, n in ranked)


def _matches(record: EvolutionOutcomeRecord, spec: EvolutionOutcomeFilterSpec) -> bool:
    if spec.agent_id is not None and record.agent_id != spec.agent_id:
        return False
    if spec.axis is not None and record.axis != spec.axis:
        return False
    if spec.applied is not None and record.applied != spec.applied:
        return False
    if spec.since is not None and record.recorded_at < spec.since:
        return False
    return not (spec.until is not None and record.recorded_at >= spec.until)


class TestDurableStoreWriteThrough:
    async def test_record_writes_to_repo_and_buffer(self) -> None:
        repo = _InMemoryOutcomeRepo()
        store = DurableEvolutionOutcomeStore(repo=repo, clock=FakeClock(start=_NOW))
        await store.record(
            agent_id=NotBlankStr("agent-1"),
            axis=NotBlankStr("identity"),
            applied=True,
            proposed_at=_NOW - timedelta(minutes=1),
        )
        # Durable log got it...
        assert len(repo.records) == 1
        assert repo.records[0].recorded_at == _NOW
        # ...and the hot buffer reflects it.
        assert await store.count() == 1

    async def test_record_survives_durable_failure(self) -> None:
        class _FailingRepo(_InMemoryOutcomeRepo):
            @override
            async def append(self, event: EvolutionOutcomeRecord) -> None:
                msg = "durable down"
                raise RuntimeError(msg)

        store = DurableEvolutionOutcomeStore(
            repo=_FailingRepo(), clock=FakeClock(start=_NOW)
        )
        await store.record(
            agent_id=NotBlankStr("agent-1"),
            axis=NotBlankStr("identity"),
            applied=True,
            proposed_at=_NOW - timedelta(minutes=1),
        )
        # The hot buffer still has the record despite the durable failure.
        assert await store.count() == 1

    async def test_rehydrate_loads_recent_into_buffer(self) -> None:
        repo = _InMemoryOutcomeRepo()
        for index in range(3):
            await repo.append(
                EvolutionOutcomeRecord(
                    agent_id=NotBlankStr(f"agent-{index}"),
                    axis=NotBlankStr("identity"),
                    applied=True,
                    proposed_at=_NOW - timedelta(minutes=5),
                    recorded_at=_NOW + timedelta(seconds=index),
                )
            )
        store = DurableEvolutionOutcomeStore(repo=repo, clock=FakeClock(start=_NOW))
        await store.rehydrate()
        assert await store.count() == 3
        summary = await store.summarize(
            since=_NOW - timedelta(hours=1), until=_NOW + timedelta(hours=1)
        )
        assert summary.total_proposals == 3
        assert summary.approval_rate == 1.0


class TestEvolutionReadService:
    async def test_list_outcomes_newest_first(self) -> None:
        repo = _InMemoryOutcomeRepo()
        for index in range(3):
            await repo.append(
                EvolutionOutcomeRecord(
                    agent_id=NotBlankStr(f"agent-{index}"),
                    axis=NotBlankStr("identity"),
                    applied=index % 2 == 0,
                    proposed_at=_NOW - timedelta(minutes=5),
                    recorded_at=_NOW + timedelta(seconds=index),
                )
            )
        service = EvolutionReadService(repo=repo)
        outcomes = await service.list_outcomes(limit=10)
        assert [o.agent_id for o in outcomes] == ["agent-2", "agent-1", "agent-0"]

    async def test_summary_rolls_up_window(self) -> None:
        repo = _InMemoryOutcomeRepo()
        await repo.append(
            EvolutionOutcomeRecord(
                agent_id=NotBlankStr("a"),
                axis=NotBlankStr("identity"),
                applied=True,
                proposed_at=_NOW - timedelta(minutes=5),
                recorded_at=_NOW,
            )
        )
        await repo.append(
            EvolutionOutcomeRecord(
                agent_id=NotBlankStr("b"),
                axis=NotBlankStr("prompt_template"),
                applied=False,
                proposed_at=_NOW - timedelta(minutes=5),
                recorded_at=_NOW,
            )
        )
        service = EvolutionReadService(repo=repo)
        summary = await service.summary(
            since=_NOW - timedelta(hours=1), until=_NOW + timedelta(hours=1)
        )
        assert summary.total_proposals == 2
        assert summary.approval_rate == 0.5

    async def test_axis_stats(self) -> None:
        repo = _InMemoryOutcomeRepo()
        for _ in range(2):
            await repo.append(
                EvolutionOutcomeRecord(
                    agent_id=NotBlankStr("a"),
                    axis=NotBlankStr("identity"),
                    applied=True,
                    proposed_at=_NOW - timedelta(minutes=5),
                    recorded_at=_NOW,
                )
            )
        service = EvolutionReadService(repo=repo)
        stats = await service.axis_stats(
            since=_NOW - timedelta(hours=1), until=_NOW + timedelta(hours=1)
        )
        assert stats == (("identity", 2),)
