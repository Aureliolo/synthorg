"""Conformance tests for ``ActivePrincipleRepository``.

Dual-backend parity: one assertion set runs against SQLite and Postgres via
the ``backend`` fixture. Covers id-keyed CRUD (save / upsert / get / delete),
the ``list_items`` newest-first ordering with pagination offset, and a full
round-trip of every scope / mode / severity enum.
"""

from datetime import UTC, datetime, timedelta
from typing import cast

import aiosqlite
import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.strategy.active_principle import (
    ActivePrinciple,
    PrincipleEvolutionMode,
    ScopeKind,
)
from synthorg.engine.strategy.models import PrincipleSeverity
from synthorg.persistence.active_principle_protocol import ActivePrincipleRepository
from synthorg.persistence.postgres.active_principle_repo import (
    PostgresActivePrincipleRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.active_principle_repo import (
    SQLiteActivePrincipleRepository,
)
from tests._shared import as_uuid

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)


def _repo(backend: PersistenceBackend) -> ActivePrincipleRepository:
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteActivePrincipleRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresActivePrincipleRepository(cast("AsyncConnectionPool", handle))
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _principle(  # noqa: PLR0913
    *,
    principle_id: str = "principle-1",
    scope: str = "all",
    scope_kind: ScopeKind = ScopeKind.ALL,
    mode: PrincipleEvolutionMode = PrincipleEvolutionMode.ORG_WIDE,
    severity: PrincipleSeverity = PrincipleSeverity.WARNING,
    created_at: datetime = _NOW,
) -> ActivePrinciple:
    return ActivePrinciple(
        id=as_uuid(principle_id),
        principle_text=NotBlankStr(f"Principle text {principle_id}"),
        scope=NotBlankStr(scope),
        scope_kind=scope_kind,
        evolution_mode=mode,
        severity=severity,
        created_at=created_at,
        updated_at=created_at,
    )


class TestActivePrincipleCrud:
    async def test_save_and_get_round_trip(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        principle = _principle(
            scope="Backend Developer",
            scope_kind=ScopeKind.ROLE,
            mode=PrincipleEvolutionMode.OVERRIDE,
            severity=PrincipleSeverity.CRITICAL,
        )
        await repo.save(principle)

        fetched = await repo.get(NotBlankStr(str(as_uuid("principle-1"))))
        assert fetched is not None
        assert fetched.scope == "Backend Developer"
        assert fetched.scope_kind is ScopeKind.ROLE
        assert fetched.evolution_mode is PrincipleEvolutionMode.OVERRIDE
        assert fetched.severity is PrincipleSeverity.CRITICAL
        assert fetched.principle_text == "Principle text principle-1"

    async def test_get_returns_none_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert await repo.get(NotBlankStr(str(as_uuid("nope")))) is None

    async def test_save_upsert_replaces(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_principle(scope="all"))
        await repo.save(
            _principle(scope="Engineering", scope_kind=ScopeKind.DEPARTMENT)
        )

        items = await repo.list_items()
        assert len(items) == 1
        assert items[0].scope == "Engineering"
        assert items[0].scope_kind is ScopeKind.DEPARTMENT

    async def test_delete(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_principle())
        key = NotBlankStr(str(as_uuid("principle-1")))
        assert await repo.delete(key) is True
        assert await repo.delete(key) is False
        assert await repo.get(key) is None


class TestActivePrincipleList:
    async def test_list_items_newest_first(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        for index in range(3):
            await repo.save(
                _principle(
                    principle_id=f"principle-{index}",
                    created_at=_NOW + timedelta(seconds=index),
                )
            )

        items = await repo.list_items()
        assert [str(p.id) for p in items] == [
            str(as_uuid("principle-2")),
            str(as_uuid("principle-1")),
            str(as_uuid("principle-0")),
        ]

    async def test_list_items_pagination_offset(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        for index in range(5):
            await repo.save(
                _principle(
                    principle_id=f"principle-{index}",
                    created_at=_NOW + timedelta(seconds=index),
                )
            )

        page = await repo.list_items(limit=2, offset=2)
        assert len(page) == 2
        # Newest-first: ids 4,3,2,1,0 -> offset 2 yields 2 then 1.
        assert [str(p.id) for p in page] == [
            str(as_uuid("principle-2")),
            str(as_uuid("principle-1")),
        ]
