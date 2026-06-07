"""Conformance tests for ``CodebaseStructureMapRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.codebase_structure_map import (
    BuildFile,
    CodebaseStructureMap,
    Dependency,
    DependencyScope,
    Ecosystem,
    EntryPoint,
    EntryPointKind,
    Module,
    ModuleKind,
)
from synthorg.core.codebase_structure_map import (
    TestSuite as SuiteEntry,  # aliased off ``Test*`` so pytest will not collect it
)
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.integration

_HASH_A = "a" * 64
_HASH_B = "b" * 64


def _project(project_id: str = "proj-1") -> Project:
    return Project(id=as_uuid(project_id), name=NotBlankStr("Demo"))


def _structure_map(
    *,
    project_id: str = "proj-1",
    source_ref: str = "https://example.com/acme/legacy.git",
    content_hash: str = _HASH_A,
) -> CodebaseStructureMap:
    ts = datetime(2026, 5, 22, tzinfo=UTC)
    return CodebaseStructureMap(
        project_id=NotBlankStr(sid(project_id)),
        source_ref=NotBlankStr(source_ref),
        modules=(
            Module(path="pkg", language=Ecosystem.PYTHON, kind=ModuleKind.PACKAGE),
        ),
        entry_points=(
            EntryPoint(
                path="pyproject.toml",
                kind=EntryPointKind.CONSOLE_SCRIPT,
                command="cli = pkg.cli:main",
            ),
        ),
        test_suites=(SuiteEntry(path="tests", framework="pytest"),),
        build_files=(BuildFile(path="pyproject.toml", tool="pyproject"),),
        dependencies=(
            Dependency(
                name="httpx",
                ecosystem=Ecosystem.PYTHON,
                scope=DependencyScope.RUNTIME,
                version_spec=">=0.27",
            ),
        ),
        scanned_at=ts,
        content_hash=content_hash,
    )


class TestCodebaseStructureMapRepository:
    async def test_save_and_get_round_trips_collections(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project())
        await backend.codebase_structure_maps.save(_structure_map())

        fetched = await backend.codebase_structure_maps.get(NotBlankStr(sid("proj-1")))
        assert fetched is not None
        assert fetched.project_id == sid("proj-1")
        assert fetched.source_ref == "https://example.com/acme/legacy.git"
        assert fetched.content_hash == _HASH_A
        assert fetched.scanned_at == datetime(2026, 5, 22, tzinfo=UTC)
        assert fetched.modules[0].path == "pkg"
        assert fetched.modules[0].kind is ModuleKind.PACKAGE
        assert fetched.entry_points[0].command == "cli = pkg.cli:main"
        assert fetched.test_suites[0].framework == "pytest"
        assert fetched.build_files[0].tool == "pyproject"
        assert fetched.dependencies[0].name == "httpx"
        assert fetched.dependencies[0].scope is DependencyScope.RUNTIME

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.codebase_structure_maps.get(NotBlankStr("ghost")) is None

    async def test_save_upsert_replaces(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.codebase_structure_maps.save(_structure_map())

        updated = _structure_map(source_ref="local:/repos/new", content_hash=_HASH_B)
        await backend.codebase_structure_maps.save(updated)

        fetched = await backend.codebase_structure_maps.get(NotBlankStr(sid("proj-1")))
        assert fetched is not None
        assert fetched.source_ref == "local:/repos/new"
        assert fetched.content_hash == _HASH_B

    async def test_empty_collections_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project())
        bare = CodebaseStructureMap(
            project_id=NotBlankStr(sid("proj-1")),
            source_ref=NotBlankStr("local:/repos/empty"),
            scanned_at=datetime(2026, 5, 22, tzinfo=UTC),
            content_hash=_HASH_A,
        )
        await backend.codebase_structure_maps.save(bare)

        fetched = await backend.codebase_structure_maps.get(NotBlankStr(sid("proj-1")))
        assert fetched is not None
        assert fetched.modules == ()
        assert fetched.dependencies == ()

    async def test_list_items_ordered_by_project_id(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project("proj-b"))
        await backend.projects.save(_project("proj-a"))
        await backend.codebase_structure_maps.save(_structure_map(project_id="proj-b"))
        await backend.codebase_structure_maps.save(_structure_map(project_id="proj-a"))

        rows = await backend.codebase_structure_maps.list_items()
        ids = [r.project_id for r in rows]
        assert ids == sorted(ids)
        assert {sid("proj-a"), sid("proj-b")} <= set(ids)

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.codebase_structure_maps.save(_structure_map())

        deleted = await backend.codebase_structure_maps.delete(
            NotBlankStr(sid("proj-1"))
        )
        assert deleted is True
        assert (
            await backend.codebase_structure_maps.get(NotBlankStr(sid("proj-1")))
            is None
        )

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        result = await backend.codebase_structure_maps.delete(NotBlankStr("ghost"))
        assert result is False

    async def test_project_delete_cascades_structure_map(
        self, backend: PersistenceBackend
    ) -> None:
        """Deleting the parent project removes its structure map (FK cascade)."""
        await backend.projects.save(_project())
        await backend.codebase_structure_maps.save(_structure_map())

        await backend.projects.delete(NotBlankStr(sid("proj-1")))

        assert (
            await backend.codebase_structure_maps.get(NotBlankStr(sid("proj-1")))
            is None
        )
