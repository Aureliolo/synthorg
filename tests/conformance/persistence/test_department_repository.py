"""Conformance tests for ``DepartmentRepository``.

Dual-backend parity: one assertion set runs against SQLite and Postgres via
the ``backend`` fixture. Covers id-keyed CRUD (save / upsert / get / delete),
the bespoke ``get_by_name`` read, the unique-name constraint, and the
``list_items`` newest-first ordering with pagination offset.
"""

from datetime import UTC, datetime, timedelta
from typing import cast

import aiosqlite
import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.organization.department_record import DepartmentRecord
from synthorg.persistence.department_protocol import DepartmentRepository
from synthorg.persistence.postgres.department_repo import PostgresDepartmentRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.department_repo import SQLiteDepartmentRepository
from tests._shared import as_uuid

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)


def _repo(backend: PersistenceBackend) -> DepartmentRepository:
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteDepartmentRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresDepartmentRepository(cast("AsyncConnectionPool", handle))
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _record(
    *,
    dept_id: str = "dept-1",
    name: str = "Engineering",
    created_at: datetime = _NOW,
) -> DepartmentRecord:
    return DepartmentRecord(
        id=as_uuid(dept_id),
        name=NotBlankStr(name),
        description=f"The {name} department.",
        created_at=created_at,
        updated_at=created_at,
    )


class TestDepartmentCrud:
    async def test_save_and_get_round_trip(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_record())

        fetched = await repo.get(NotBlankStr(str(as_uuid("dept-1"))))
        assert fetched is not None
        assert fetched.name == "Engineering"
        assert fetched.description == "The Engineering department."

    async def test_get_by_name(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_record())
        fetched = await repo.get_by_name(NotBlankStr("Engineering"))
        assert fetched is not None
        assert str(fetched.id) == str(as_uuid("dept-1"))
        assert await repo.get_by_name(NotBlankStr("Nope")) is None

    async def test_unique_name_constraint(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_record(dept_id="dept-1", name="Engineering"))
        with pytest.raises(QueryError):
            await repo.save(_record(dept_id="dept-2", name="Engineering"))

    async def test_delete(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_record())
        key = NotBlankStr(str(as_uuid("dept-1")))
        assert await repo.delete(key) is True
        assert await repo.delete(key) is False
        assert await repo.get(key) is None


class TestDepartmentList:
    async def test_list_items_newest_first(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        for index in range(3):
            await repo.save(
                _record(
                    dept_id=f"dept-{index}",
                    name=f"Dept {index}",
                    created_at=_NOW + timedelta(seconds=index),
                )
            )

        items = await repo.list_items()
        assert [d.name for d in items] == ["Dept 2", "Dept 1", "Dept 0"]

    async def test_list_items_pagination_offset(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        for index in range(4):
            await repo.save(
                _record(
                    dept_id=f"dept-{index}",
                    name=f"Dept {index}",
                    created_at=_NOW + timedelta(seconds=index),
                )
            )

        page = await repo.list_items(limit=2, offset=1)
        assert [d.name for d in page] == ["Dept 2", "Dept 1"]
