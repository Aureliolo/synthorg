"""Conformance tests for ``AbTestRepository``.

Dual-backend parity: a single assertion set runs against SQLite and
Postgres via the ``backend`` fixture. The repo is built over the
migrated ``backend.get_db()`` handle.

Covers id-keyed CRUD (save / upsert running->terminal / get / delete)
and the newest-first ``list_items`` ordering that backs the
``GET /meta/ab-tests`` endpoint.
"""

from datetime import UTC, datetime, timedelta
from typing import cast

import aiosqlite
import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.rollout.ab_models import (
    AbTestArm,
    AbTestRecord,
    AbTestStatus,
    ABTestVerdict,
)
from synthorg.persistence.ab_test_protocol import AbTestRepository
from synthorg.persistence.postgres.ab_test_repo import PostgresAbTestRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.ab_test_repo import SQLiteAbTestRepository

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def _repo(backend: PersistenceBackend) -> AbTestRepository:
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteAbTestRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresAbTestRepository(cast("AsyncConnectionPool", handle))
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _record(  # noqa: PLR0913
    *,
    record_id: str = "proposal-1",
    name: str = "ab_test proposal-1",
    status: AbTestStatus = AbTestStatus.RUNNING,
    verdict: ABTestVerdict | None = None,
    created_at: datetime = _NOW,
    updated_at: datetime = _NOW,
) -> AbTestRecord:
    return AbTestRecord(
        id=NotBlankStr(record_id),
        name=NotBlankStr(name),
        status=status,
        arms=(
            AbTestArm(name=NotBlankStr("control"), agent_count=5, fraction=0.5),
            AbTestArm(name=NotBlankStr("treatment"), agent_count=5, fraction=0.5),
        ),
        verdict=verdict,
        observation_hours_elapsed=12.0,
        created_at=created_at,
        updated_at=updated_at,
    )


class TestAbTestCrud:
    async def test_save_and_get_round_trip(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_record())

        fetched = await repo.get(NotBlankStr("proposal-1"))
        assert fetched is not None
        assert fetched.status is AbTestStatus.RUNNING
        assert [arm.name for arm in fetched.arms] == ["control", "treatment"]
        assert fetched.arms[0].agent_count == 5
        assert fetched.observation_hours_elapsed == 12.0
        assert fetched.verdict is None

    async def test_get_returns_none_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert await repo.get(NotBlankStr("nope")) is None

    async def test_save_upsert_running_to_terminal(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_record(status=AbTestStatus.RUNNING))
        await repo.save(
            _record(
                status=AbTestStatus.COMPLETED,
                verdict=ABTestVerdict.TREATMENT_WINS,
                updated_at=_NOW + timedelta(hours=1),
            )
        )

        fetched = await repo.get(NotBlankStr("proposal-1"))
        assert fetched is not None
        assert fetched.status is AbTestStatus.COMPLETED
        assert fetched.verdict is ABTestVerdict.TREATMENT_WINS
        # created_at is preserved across the upsert; updated_at advances.
        assert fetched.created_at == _NOW
        assert fetched.updated_at == _NOW + timedelta(hours=1)

    async def test_delete(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_record())
        assert await repo.delete(NotBlankStr("proposal-1")) is True
        assert await repo.delete(NotBlankStr("proposal-1")) is False
        assert await repo.get(NotBlankStr("proposal-1")) is None


class TestAbTestList:
    async def test_list_items_newest_first(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        for index in range(3):
            await repo.save(
                _record(
                    record_id=f"proposal-{index}",
                    name=f"ab_test proposal-{index}",
                    created_at=_NOW + timedelta(seconds=index),
                )
            )

        page = await repo.list_items(limit=2, offset=0)
        assert [r.id for r in page] == ["proposal-2", "proposal-1"]
