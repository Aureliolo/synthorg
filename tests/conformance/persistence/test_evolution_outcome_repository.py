"""Conformance tests for ``EvolutionOutcomeRepository``.

Dual-backend parity: one assertion set runs against SQLite and Postgres
via the ``backend`` fixture. Covers append + newest-first query, the
filter spec (agent / axis / applied / window), pagination offset,
``axis_counts`` aggregation, and ``purge_before`` retention.
"""

from datetime import UTC, datetime, timedelta
from typing import cast

import aiosqlite
import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.evolution.outcome_models import EvolutionOutcomeRecord
from synthorg.persistence.evolution_outcome_protocol import (
    EvolutionOutcomeFilterSpec,
    EvolutionOutcomeRepository,
)
from synthorg.persistence.postgres.evolution_outcome_repo import (
    PostgresEvolutionOutcomeRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.evolution_outcome_repo import (
    SQLiteEvolutionOutcomeRepository,
)

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def _repo(backend: PersistenceBackend) -> EvolutionOutcomeRepository:
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteEvolutionOutcomeRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresEvolutionOutcomeRepository(cast("AsyncConnectionPool", handle))
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _record(
    *,
    agent_id: str = "agent-1",
    axis: str = "identity",
    applied: bool = True,
    recorded_at: datetime = _NOW,
) -> EvolutionOutcomeRecord:
    return EvolutionOutcomeRecord(
        agent_id=NotBlankStr(agent_id),
        axis=NotBlankStr(axis),
        applied=applied,
        proposed_at=recorded_at - timedelta(minutes=5),
        recorded_at=recorded_at,
    )


class TestEvolutionOutcomeAppendQuery:
    async def test_append_round_trip_newest_first(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        for index in range(3):
            await repo.append(
                _record(
                    agent_id=f"agent-{index}",
                    recorded_at=_NOW + timedelta(seconds=index),
                )
            )
        items = await repo.query(EvolutionOutcomeFilterSpec())
        assert [r.agent_id for r in items] == ["agent-2", "agent-1", "agent-0"]
        # Timestamps round-trip to the same instant on both backends.
        assert items[0].recorded_at == _NOW + timedelta(seconds=2)
        assert items[0].applied is True

    async def test_filter_by_agent_axis_applied(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.append(_record(agent_id="a", axis="identity", applied=True))
        await repo.append(_record(agent_id="a", axis="prompt_template", applied=False))
        await repo.append(_record(agent_id="b", axis="identity", applied=True))

        by_agent = await repo.query(
            EvolutionOutcomeFilterSpec(agent_id=NotBlankStr("a"))
        )
        assert {r.agent_id for r in by_agent} == {"a"}
        assert len(by_agent) == 2

        by_axis = await repo.query(
            EvolutionOutcomeFilterSpec(axis=NotBlankStr("identity"))
        )
        assert {r.axis for r in by_axis} == {"identity"}
        assert len(by_axis) == 2

        rejected = await repo.query(EvolutionOutcomeFilterSpec(applied=False))
        assert len(rejected) == 1
        assert rejected[0].axis == "prompt_template"

    async def test_pagination_offset(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        for index in range(5):
            await repo.append(
                _record(
                    agent_id=f"agent-{index}",
                    recorded_at=_NOW + timedelta(seconds=index),
                )
            )
        first = await repo.query(EvolutionOutcomeFilterSpec(), limit=2, offset=0)
        second = await repo.query(EvolutionOutcomeFilterSpec(), limit=2, offset=2)
        assert [r.agent_id for r in first] == ["agent-4", "agent-3"]
        assert [r.agent_id for r in second] == ["agent-2", "agent-1"]

    async def test_window_filter(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        for index in range(4):
            await repo.append(_record(recorded_at=_NOW + timedelta(hours=index)))
        window = await repo.query(
            EvolutionOutcomeFilterSpec(
                since=_NOW + timedelta(hours=1),
                until=_NOW + timedelta(hours=3),
            )
        )
        assert len(window) == 2


class TestEvolutionOutcomeAggregation:
    async def test_axis_counts(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        for _ in range(3):
            await repo.append(_record(axis="identity"))
        await repo.append(_record(axis="prompt_template"))

        counts = await repo.axis_counts(
            since=_NOW - timedelta(hours=1),
            until=_NOW + timedelta(hours=1),
        )
        assert counts[0] == ("identity", 3)
        assert ("prompt_template", 1) in counts

    async def test_purge_before(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.append(_record(recorded_at=_NOW))
        await repo.append(_record(recorded_at=_NOW + timedelta(days=2)))

        removed = await repo.purge_before(_NOW + timedelta(days=1))
        assert removed == 1
        remaining = await repo.query(EvolutionOutcomeFilterSpec())
        assert len(remaining) == 1
        assert remaining[0].recorded_at == _NOW + timedelta(days=2)
