"""Conformance tests for ``ProjectEnvironmentRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import EnvironmentType
from synthorg.core.project import Project
from synthorg.core.project_environment import ProjectEnvironment
from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.integration


def _project(project_id: str = "proj-1") -> Project:
    return Project(id=as_uuid(project_id), name=NotBlankStr("Demo"))


def _environment(
    *,
    project_id: str = "proj-1",
    env_type: EnvironmentType = EnvironmentType.MANIFEST,
    declaration_hash: str = "a" * 64,
    image_ref: str | None = None,
) -> ProjectEnvironment:
    ts = datetime(2026, 5, 21, tzinfo=UTC)
    if image_ref is None and env_type is EnvironmentType.DEVCONTAINER:
        image_ref = f"synthorg-project-{project_id.lower()}:abc123"
    return ProjectEnvironment(
        project_id=NotBlankStr(sid(project_id)),
        environment_type=env_type,
        declaration_hash=NotBlankStr(declaration_hash),
        image_ref=NotBlankStr(image_ref) if image_ref else None,
        provisioned_at=ts,
        updated_at=ts,
    )


class TestProjectEnvironmentRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.project_environments.save(_environment())

        fetched = await backend.project_environments.get(NotBlankStr(sid("proj-1")))
        assert fetched is not None
        assert fetched.project_id == sid("proj-1")
        assert fetched.environment_type is EnvironmentType.MANIFEST
        assert fetched.declaration_hash == "a" * 64
        assert fetched.image_ref is None
        assert fetched.provisioned_at == datetime(2026, 5, 21, tzinfo=UTC)

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.project_environments.get(NotBlankStr("ghost")) is None

    async def test_save_upsert_replaces(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.project_environments.save(_environment())

        rebuilt = _environment(
            env_type=EnvironmentType.NIX,
            declaration_hash="b" * 64,
        )
        await backend.project_environments.save(rebuilt)

        fetched = await backend.project_environments.get(NotBlankStr(sid("proj-1")))
        assert fetched is not None
        assert fetched.environment_type is EnvironmentType.NIX
        assert fetched.declaration_hash == "b" * 64

    async def test_image_ref_round_trips(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.project_environments.save(
            _environment(
                env_type=EnvironmentType.DEVCONTAINER,
                image_ref="synthorg-project-proj-1:abc123def456",
            ),
        )

        fetched = await backend.project_environments.get(NotBlankStr(sid("proj-1")))
        assert fetched is not None
        assert fetched.image_ref == "synthorg-project-proj-1:abc123def456"

    async def test_image_ref_none_round_trips(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project())
        await backend.project_environments.save(_environment())

        fetched = await backend.project_environments.get(NotBlankStr(sid("proj-1")))
        assert fetched is not None
        assert fetched.image_ref is None

    async def test_list_items_ordered_by_project_id(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project("proj-b"))
        await backend.projects.save(_project("proj-a"))
        await backend.project_environments.save(_environment(project_id="proj-b"))
        await backend.project_environments.save(_environment(project_id="proj-a"))

        rows = await backend.project_environments.list_items()
        ids = [r.project_id for r in rows]
        assert ids == sorted(ids)
        assert {sid("proj-a"), sid("proj-b")} <= set(ids)

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.project_environments.save(_environment())

        deleted = await backend.project_environments.delete(NotBlankStr(sid("proj-1")))
        assert deleted is True
        assert (
            await backend.project_environments.get(NotBlankStr(sid("proj-1"))) is None
        )

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        assert (
            await backend.project_environments.delete(NotBlankStr("ghost"))
        ) is False

    async def test_project_delete_cascades_environment(
        self, backend: PersistenceBackend
    ) -> None:
        """Deleting the parent project removes its environment row (FK cascade)."""
        await backend.projects.save(_project())
        await backend.project_environments.save(_environment())

        await backend.projects.delete(NotBlankStr(sid("proj-1")))

        assert (
            await backend.project_environments.get(NotBlankStr(sid("proj-1"))) is None
        )
