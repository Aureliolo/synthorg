"""Conformance tests for ``BenchmarkScoreRepository``.

Dual-backend parity: a single assertion set runs against SQLite and
Postgres via the ``backend`` fixture in
``tests/conformance/persistence/conftest.py``. The repo is built over
the migrated ``backend.get_db()`` handle.

Covers:

* CRUD round-trip (save / get / list / delete).
* ``get`` returns ``None`` for an absent model.
* ``save`` upsert semantics: re-recording a model replaces the row.
* ``list_items`` ordering (``model_id`` ASC) + pagination.
* Invalid pagination args raise :class:`QueryError`.
"""

from datetime import UTC, datetime
from typing import cast

import aiosqlite
import pytest

from synthorg.budget.benchmark_models import BenchmarkScoreRecord
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.benchmark_score_protocol import BenchmarkScoreRepository
from synthorg.persistence.postgres.benchmark_score_repo import (
    PostgresBenchmarkScoreRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.benchmark_score_repo import (
    SQLiteBenchmarkScoreRepository,
)

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def _repo(backend: PersistenceBackend) -> BenchmarkScoreRepository:
    """Return a concrete benchmark-score repository bound to *backend*."""
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteBenchmarkScoreRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresBenchmarkScoreRepository(
            cast("AsyncConnectionPool", handle),
        )
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _make_record(  # noqa: PLR0913 -- test helper carries every record field
    *,
    model_id: str = "example-large-001",
    score: float = 92.0,
    confidence_lower: float | None = None,
    confidence_upper: float | None = None,
    source: str = "benchmark:measured-v1",
    suite_version: str = "sha256:abc123",
    cassette_sha256: str = "deadbeef",
) -> BenchmarkScoreRecord:
    # Default the band to bracket the score so callers passing only a
    # score get a valid record (the model enforces lower <= score <= upper).
    lower = confidence_lower if confidence_lower is not None else max(0.0, score - 4.0)
    upper = (
        confidence_upper if confidence_upper is not None else min(100.0, score + 3.0)
    )
    return BenchmarkScoreRecord(
        model_id=NotBlankStr(model_id),
        score=score,
        confidence_lower=lower,
        confidence_upper=upper,
        source=NotBlankStr(source),
        suite_version=NotBlankStr(suite_version),
        cassette_sha256=NotBlankStr(cassette_sha256),
        last_updated=_NOW,
    )


class TestBenchmarkScoreRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        record = _make_record()
        await repo.save(record)

        fetched = await repo.get(NotBlankStr("example-large-001"))
        assert fetched is not None
        assert fetched.model_id == "example-large-001"
        assert fetched.score == pytest.approx(92.0)
        assert fetched.confidence_lower == pytest.approx(88.0)
        assert fetched.confidence_upper == pytest.approx(95.0)
        assert fetched.source == "benchmark:measured-v1"
        assert fetched.suite_version == "sha256:abc123"
        assert fetched.cassette_sha256 == "deadbeef"
        assert fetched.last_updated.tzinfo is not None

    async def test_get_returns_none_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert await repo.get(NotBlankStr("example-missing-999")) is None

    async def test_save_upsert_replaces_existing(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_make_record(score=92.0, confidence_upper=95.0))
        await repo.save(_make_record(score=80.0, confidence_lower=70.0))

        fetched = await repo.get(NotBlankStr("example-large-001"))
        assert fetched is not None
        assert fetched.score == pytest.approx(80.0)
        assert fetched.confidence_lower == pytest.approx(70.0)
        items = await repo.list_items()
        assert len([r for r in items if r.model_id == "example-large-001"]) == 1

    async def test_list_items_ordered_and_paginated(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_make_record(model_id="example-small-001", score=72.0))
        await repo.save(_make_record(model_id="example-large-001", score=92.0))
        await repo.save(_make_record(model_id="example-medium-001", score=85.0))

        items = await repo.list_items()
        ids = [r.model_id for r in items]
        assert ids == [
            "example-large-001",
            "example-medium-001",
            "example-small-001",
        ]

        page = await repo.list_items(limit=1, offset=1)
        assert [r.model_id for r in page] == ["example-medium-001"]

    async def test_delete(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_make_record())
        assert await repo.delete(NotBlankStr("example-large-001")) is True
        assert await repo.delete(NotBlankStr("example-large-001")) is False
        assert await repo.get(NotBlankStr("example-large-001")) is None

    async def test_list_items_rejects_invalid_pagination(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        with pytest.raises(QueryError):
            await repo.list_items(limit=-1)
