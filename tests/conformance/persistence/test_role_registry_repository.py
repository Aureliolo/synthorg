"""Conformance tests for ``RoleRegistryRepository``.

Dual-backend parity: one assertion set runs against SQLite and Postgres via
the ``backend`` fixture. Covers id-keyed CRUD keyed by ``role.name`` (save /
upsert / get / delete), the ``list_items`` alphabetical ordering with
pagination offset, the ``is_builtin`` flag, and a full round-trip of the role's
JSON tuple fields and enum fields.
"""

from datetime import UTC, datetime
from typing import cast

import aiosqlite
import pytest

from synthorg.core.role import Role
from synthorg.core.role_record import RoleRecord
from synthorg.core.types import NotBlankStr
from synthorg.hr.seniority import SeniorityLevel
from synthorg.organization.enums import DepartmentName
from synthorg.persistence.postgres.role_registry_repo import (
    PostgresRoleRegistryRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.role_registry_protocol import RoleRegistryRepository
from synthorg.persistence.sqlite.role_registry_repo import (
    SQLiteRoleRegistryRepository,
)

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)


def _repo(backend: PersistenceBackend) -> RoleRegistryRepository:
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteRoleRegistryRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresRoleRegistryRepository(cast("AsyncConnectionPool", handle))
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _record(
    *,
    name: str = "Backend Developer",
    department: DepartmentName = DepartmentName.ENGINEERING,
    is_builtin: bool = False,
) -> RoleRecord:
    role = Role(
        name=NotBlankStr(name),
        department=department,
        required_skills=(NotBlankStr("python"), NotBlankStr("sql")),
        authority_level=SeniorityLevel.SENIOR,
        tool_access=(NotBlankStr("git"),),
        description=f"Role {name}.",
    )
    return RoleRecord(
        role=role, is_builtin=is_builtin, created_at=_NOW, updated_at=_NOW
    )


class TestRoleRegistryCrud:
    async def test_save_and_get_round_trip(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_record(is_builtin=True))

        fetched = await repo.get(NotBlankStr("Backend Developer"))
        assert fetched is not None
        assert fetched.is_builtin is True
        assert fetched.role.department is DepartmentName.ENGINEERING
        assert fetched.role.authority_level is SeniorityLevel.SENIOR
        assert fetched.role.required_skills == ("python", "sql")
        assert fetched.role.tool_access == ("git",)

    async def test_get_returns_none_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert await repo.get(NotBlankStr("nope")) is None

    async def test_save_upsert_replaces_by_name(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_record(is_builtin=True))
        await repo.save(_record(department=DepartmentName.PRODUCT, is_builtin=False))

        items = await repo.list_items()
        assert len(items) == 1
        assert items[0].role.department is DepartmentName.PRODUCT
        assert items[0].is_builtin is False

    async def test_delete(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_record())
        assert await repo.delete(NotBlankStr("Backend Developer")) is True
        assert await repo.delete(NotBlankStr("Backend Developer")) is False
        assert await repo.get(NotBlankStr("Backend Developer")) is None


class TestRoleRegistryList:
    async def test_list_items_alphabetical(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        for name in ("Carol Role", "Alice Role", "Bob Role"):
            await repo.save(_record(name=name))

        items = await repo.list_items()
        assert [r.role.name for r in items] == ["Alice Role", "Bob Role", "Carol Role"]

    async def test_list_items_pagination_offset(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        for name in ("Alice Role", "Bob Role", "Carol Role", "Dan Role"):
            await repo.save(_record(name=name))

        page = await repo.list_items(limit=2, offset=1)
        assert [r.role.name for r in page] == ["Bob Role", "Carol Role"]
