"""Conformance tests for ``ProviderLatchRepository``.

Dual-backend parity: one assertion set runs against SQLite and Postgres via
the ``backend`` fixture. Covers the round trip that is the whole point (a
refusal read back after the process that recorded it is gone), the upsert
that keeps one row per pair, pair-scoped reads and deletes, deterministic
ordering, and an owner-less refusal round-tripping as ``None`` rather than
as some invented id.
"""

from datetime import UTC, datetime, timedelta
from typing import cast

import aiosqlite
import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.postgres.provider_latch_repo import (
    PostgresProviderLatchRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.provider_latch_protocol import ProviderLatchRepository
from synthorg.persistence.sqlite.provider_latch_repo import (
    SQLiteProviderLatchRepository,
)
from synthorg.providers.health import ProviderOutcomeClass
from synthorg.providers.latch import LatchedFailure

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def _repo(backend: PersistenceBackend) -> ProviderLatchRepository:
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteProviderLatchRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresProviderLatchRepository(cast("AsyncConnectionPool", handle))
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _latch(
    *,
    provider: str = "example-provider",
    model: str = "example-expert-001",
    occurred_at: datetime = _NOW,
    error_message: str = "insufficient balance",
    owner: tuple[str, str] | None = ("agent-7", "task-9"),
) -> LatchedFailure:
    return LatchedFailure(
        provider_name=NotBlankStr(provider),
        model=NotBlankStr(model),
        outcome_class=ProviderOutcomeClass.PAYMENT_REQUIRED,
        occurred_at=occurred_at,
        error_message=NotBlankStr(error_message),
        response_time_ms=311.0,
        agent_id=None if owner is None else NotBlankStr(owner[0]),
        task_id=None if owner is None else NotBlankStr(owner[1]),
    )


class TestRoundTrip:
    async def test_a_refusal_survives_the_process_that_recorded_it(
        self, backend: PersistenceBackend
    ) -> None:
        # The whole point: nothing here shares memory with the recorder.
        latch = _latch()
        await _repo(backend).save(latch)

        read = await _repo(backend).get(("example-provider", "example-expert-001"))

        assert read == latch

    async def test_an_unowned_refusal_keeps_no_invented_owner(
        self, backend: PersistenceBackend
    ) -> None:
        await _repo(backend).save(_latch(owner=None))

        read = await _repo(backend).get(("example-provider", "example-expert-001"))

        assert read is not None
        assert read.agent_id is None
        assert read.task_id is None

    async def test_an_unlatched_pair_reads_none(
        self, backend: PersistenceBackend
    ) -> None:
        assert await _repo(backend).get(("example-provider", "never-called")) is None


class TestOneRowPerPair:
    async def test_a_fresh_refusal_replaces_the_one_before_it(
        self, backend: PersistenceBackend
    ) -> None:
        # The reader honours the newest and nothing else, so a second row for
        # the same pair would be a row nothing can ever consult.
        repo = _repo(backend)
        await repo.save(_latch(error_message="first"))
        later = _NOW + timedelta(minutes=5)
        await repo.save(_latch(occurred_at=later, error_message="second"))

        rows = await repo.list_items()

        assert len(rows) == 1
        assert rows[0].occurred_at == later
        assert rows[0].error_message == "second"

    async def test_two_models_on_one_connection_latch_apart(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_latch(model="example-expert-001"))
        await repo.save(_latch(model="example-capable-001"))

        rows = await repo.list_items()

        assert [str(row.model) for row in rows] == [
            "example-capable-001",
            "example-expert-001",
        ]


class TestDelete:
    async def test_delete_removes_only_the_named_pair(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_latch(model="example-expert-001"))
        await repo.save(_latch(model="example-capable-001"))

        removed = await repo.delete(("example-provider", "example-expert-001"))

        assert removed is True
        assert [str(row.model) for row in await repo.list_items()] == [
            "example-capable-001"
        ]

    async def test_deleting_an_absent_pair_reports_false(
        self, backend: PersistenceBackend
    ) -> None:
        assert await _repo(backend).delete(("example-provider", "gone")) is False
