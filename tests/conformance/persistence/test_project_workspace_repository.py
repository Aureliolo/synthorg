"""Conformance tests for ``ProjectWorkspaceRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import GitBackendType
from synthorg.core.project import Project
from synthorg.core.project_workspace import ProjectWorkspace
from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _project(project_id: str = "proj-1") -> Project:
    return Project(id=NotBlankStr(project_id), name=NotBlankStr("Demo"))


def _workspace(
    *,
    project_id: str = "proj-1",
    workspace_path: str = "/data/projects/proj-1",
    kind: GitBackendType = GitBackendType.EMBEDDED,
    remote_ref: str | None = None,
    default_branch: str = "main",
) -> ProjectWorkspace:
    ts = datetime(2026, 5, 19, tzinfo=UTC)
    return ProjectWorkspace(
        project_id=NotBlankStr(project_id),
        workspace_path=NotBlankStr(workspace_path),
        git_backend_kind=kind,
        remote_ref=NotBlankStr(remote_ref) if remote_ref else None,
        default_branch=NotBlankStr(default_branch),
        created_at=ts,
        updated_at=ts,
    )


class TestProjectWorkspaceRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.project_workspaces.save(_workspace())

        fetched = await backend.project_workspaces.get(NotBlankStr("proj-1"))
        assert fetched is not None
        assert fetched.project_id == "proj-1"
        assert fetched.workspace_path == "/data/projects/proj-1"
        assert fetched.git_backend_kind is GitBackendType.EMBEDDED
        assert fetched.default_branch == "main"
        assert fetched.created_at == datetime(2026, 5, 19, tzinfo=UTC)

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.project_workspaces.get(NotBlankStr("ghost")) is None

    async def test_save_upsert_replaces(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.project_workspaces.save(_workspace())

        moved = _workspace(
            workspace_path="/data/projects/proj-1",
            kind=GitBackendType.LOCAL_PATH,
        )
        await backend.project_workspaces.save(moved)

        fetched = await backend.project_workspaces.get(NotBlankStr("proj-1"))
        assert fetched is not None
        assert fetched.git_backend_kind is GitBackendType.LOCAL_PATH

    async def test_remote_ref_round_trips(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.project_workspaces.save(
            _workspace(
                kind=GitBackendType.EXTERNAL_REMOTE,
                remote_ref="github-main",
            ),
        )

        fetched = await backend.project_workspaces.get(NotBlankStr("proj-1"))
        assert fetched is not None
        assert fetched.remote_ref == "github-main"

    async def test_remote_ref_none_round_trips(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project())
        await backend.project_workspaces.save(_workspace())

        fetched = await backend.project_workspaces.get(NotBlankStr("proj-1"))
        assert fetched is not None
        assert fetched.remote_ref is None

    async def test_list_items_ordered_by_project_id(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project("proj-b"))
        await backend.projects.save(_project("proj-a"))
        await backend.project_workspaces.save(
            _workspace(project_id="proj-b", workspace_path="/d/b")
        )
        await backend.project_workspaces.save(
            _workspace(project_id="proj-a", workspace_path="/d/a")
        )

        rows = await backend.project_workspaces.list_items()
        ids = [r.project_id for r in rows]
        assert ids == sorted(ids)
        assert {"proj-a", "proj-b"} <= set(ids)

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        await backend.project_workspaces.save(_workspace())

        deleted = await backend.project_workspaces.delete(NotBlankStr("proj-1"))
        assert deleted is True
        assert await backend.project_workspaces.get(NotBlankStr("proj-1")) is None

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        assert (await backend.project_workspaces.delete(NotBlankStr("ghost"))) is False

    async def test_project_delete_cascades_workspace(
        self, backend: PersistenceBackend
    ) -> None:
        """Deleting the parent project removes its workspace row (FK cascade)."""
        await backend.projects.save(_project())
        await backend.project_workspaces.save(_workspace())

        await backend.projects.delete(NotBlankStr("proj-1"))

        assert await backend.project_workspaces.get(NotBlankStr("proj-1")) is None
