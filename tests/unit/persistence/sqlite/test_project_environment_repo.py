"""Unit tests for SQLiteProjectEnvironmentRepository (migrated in-memory DB)."""

from datetime import UTC, datetime

import aiosqlite
import pytest

from synthorg.core.enums import EnvironmentType
from synthorg.core.project import Project
from synthorg.core.project_environment import ProjectEnvironment
from synthorg.core.types import NotBlankStr
from synthorg.persistence.project_environment_protocol import (
    ProjectEnvironmentRepository,
)
from synthorg.persistence.sqlite.project_environment_repo import (
    SQLiteProjectEnvironmentRepository,
)
from synthorg.persistence.sqlite.project_repo import SQLiteProjectRepository
from tests._shared import as_uuid, sid
from tests._shared.persistence import make_private_write_context

pytestmark = pytest.mark.unit


def _environment(
    *,
    project_id: str = "proj-1",
    env_type: EnvironmentType = EnvironmentType.MANIFEST,
    declaration_hash: str = "a" * 64,
    image_ref: str | None = None,
) -> ProjectEnvironment:
    ts = datetime(2026, 5, 21, tzinfo=UTC)
    if image_ref is None and env_type is EnvironmentType.DEVCONTAINER:
        image_ref = "synthorg-project-proj-1:abc123"
    return ProjectEnvironment(
        project_id=NotBlankStr(sid(project_id)),
        environment_type=env_type,
        declaration_hash=NotBlankStr(declaration_hash),
        image_ref=NotBlankStr(image_ref) if image_ref else None,
        provisioned_at=ts,
        updated_at=ts,
    )


async def _seed_project(db: aiosqlite.Connection, project_id: str = "proj-1") -> None:
    repo = SQLiteProjectRepository(db, write_context=make_private_write_context())
    await repo.save(Project(id=as_uuid(project_id), name=NotBlankStr("Demo")))


class TestSQLiteProjectEnvironmentRepository:
    async def test_satisfies_protocol(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteProjectEnvironmentRepository(
            migrated_db, write_context=make_private_write_context()
        )
        assert isinstance(repo, ProjectEnvironmentRepository)

    async def test_save_and_get(self, migrated_db: aiosqlite.Connection) -> None:
        await _seed_project(migrated_db)
        repo = SQLiteProjectEnvironmentRepository(
            migrated_db, write_context=make_private_write_context()
        )
        await repo.save(_environment())

        fetched = await repo.get(NotBlankStr(sid("proj-1")))
        assert fetched is not None
        assert fetched.environment_type is EnvironmentType.MANIFEST
        assert fetched.declaration_hash == "a" * 64
        assert fetched.image_ref is None
        assert fetched.provisioned_at == datetime(2026, 5, 21, tzinfo=UTC)

    async def test_upsert_replaces(self, migrated_db: aiosqlite.Connection) -> None:
        await _seed_project(migrated_db)
        repo = SQLiteProjectEnvironmentRepository(
            migrated_db, write_context=make_private_write_context()
        )
        await repo.save(_environment())
        await repo.save(
            _environment(
                env_type=EnvironmentType.DEVCONTAINER, declaration_hash="b" * 64
            )
        )

        fetched = await repo.get(NotBlankStr(sid("proj-1")))
        assert fetched is not None
        assert fetched.environment_type is EnvironmentType.DEVCONTAINER
        assert fetched.declaration_hash == "b" * 64

    async def test_get_missing(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteProjectEnvironmentRepository(
            migrated_db, write_context=make_private_write_context()
        )
        assert await repo.get(NotBlankStr(sid("ghost"))) is None

    async def test_delete(self, migrated_db: aiosqlite.Connection) -> None:
        await _seed_project(migrated_db)
        repo = SQLiteProjectEnvironmentRepository(
            migrated_db, write_context=make_private_write_context()
        )
        await repo.save(_environment())

        assert await repo.delete(NotBlankStr(sid("proj-1"))) is True
        assert await repo.delete(NotBlankStr(sid("proj-1"))) is False

    async def test_list_ordered(self, migrated_db: aiosqlite.Connection) -> None:
        await _seed_project(migrated_db, "proj-b")
        await _seed_project(migrated_db, "proj-a")
        repo = SQLiteProjectEnvironmentRepository(
            migrated_db, write_context=make_private_write_context()
        )
        await repo.save(_environment(project_id="proj-b"))
        await repo.save(_environment(project_id="proj-a"))

        rows = await repo.list_items()
        ids = [r.project_id for r in rows]
        assert ids == sorted(ids)
