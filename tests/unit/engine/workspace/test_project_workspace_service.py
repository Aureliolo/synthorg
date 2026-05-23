"""Unit tests for ``ProjectWorkspaceService.get_or_provision``."""

import asyncio
from pathlib import Path

import pytest

from synthorg.core.enums import GitBackendType
from synthorg.core.project_workspace import ProjectWorkspace
from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.git_backend import (
    GitBackend,
    GitBackendConfig,
    ProvisionResult,
)
from synthorg.engine.workspace.project_workspace_service import (
    ProjectWorkspaceService,
)
from tests._shared import FakeClock, mock_of

pytestmark = pytest.mark.unit


class _FakeWorkspaceRepo:
    """Minimal in-memory ProjectWorkspaceRepository for stateful tests."""

    def __init__(self) -> None:
        self._rows: dict[str, ProjectWorkspace] = {}
        self.save_calls = 0

    async def save(self, entity: ProjectWorkspace) -> None:
        self.save_calls += 1
        self._rows[entity.project_id] = entity

    async def get(self, entity_id: NotBlankStr) -> ProjectWorkspace | None:
        return self._rows.get(entity_id)

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ProjectWorkspace, ...]:
        return tuple(self._rows.values())[offset : offset + limit]

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._rows.pop(entity_id, None) is not None


def _git_backend(kind: GitBackendType = GitBackendType.EMBEDDED) -> GitBackend:
    backend = mock_of[GitBackend]()
    backend.get_backend_type.return_value = kind

    async def _provision(
        *,
        project_id: NotBlankStr,
        workspace_path: Path,
        default_branch: NotBlankStr,
    ) -> ProvisionResult:
        return ProvisionResult(
            repo_root=NotBlankStr(str(workspace_path)),
            default_branch=default_branch,
            newly_created=True,
        )

    backend.provision.side_effect = _provision
    return backend  # type: ignore[no-any-return]


def _service(
    tmp_path: Path,
    repo: _FakeWorkspaceRepo,
    backend: GitBackend,
    *,
    kind: GitBackendType = GitBackendType.EMBEDDED,
) -> ProjectWorkspaceService:
    return ProjectWorkspaceService(
        base_root=tmp_path,
        repo=repo,
        git_backend=backend,
        config=GitBackendConfig(kind=kind)
        if kind is GitBackendType.EMBEDDED
        else GitBackendConfig(kind=kind, local_repo_path=str(tmp_path / "byo")),
        clock=FakeClock(),
    )


class TestProjectWorkspaceService:
    async def test_provisions_new_workspace(self, tmp_path: Path) -> None:
        repo = _FakeWorkspaceRepo()
        backend = _git_backend()
        svc = _service(tmp_path, repo, backend)

        ws = await svc.get_or_provision(NotBlankStr("proj-1"))

        assert ws.project_id == "proj-1"
        assert ws.git_backend_kind is GitBackendType.EMBEDDED
        assert ws.workspace_path == str(tmp_path / "projects" / "proj-1")
        backend.provision.assert_awaited_once()  # type: ignore[attr-defined]
        assert repo.save_calls == 1

    async def test_idempotent_same_kind_short_circuits(self, tmp_path: Path) -> None:
        repo = _FakeWorkspaceRepo()
        backend = _git_backend()
        svc = _service(tmp_path, repo, backend)

        first = await svc.get_or_provision(NotBlankStr("proj-1"))
        second = await svc.get_or_provision(NotBlankStr("proj-1"))

        assert first == second
        backend.provision.assert_awaited_once()  # type: ignore[attr-defined]
        assert repo.save_calls == 1

    async def test_backend_kind_change_reprovisions(self, tmp_path: Path) -> None:
        repo = _FakeWorkspaceRepo()
        emb = _git_backend(GitBackendType.EMBEDDED)
        svc1 = _service(tmp_path, repo, emb)
        await svc1.get_or_provision(NotBlankStr("proj-1"))

        local = _git_backend(GitBackendType.LOCAL_PATH)
        svc2 = _service(tmp_path, repo, local, kind=GitBackendType.LOCAL_PATH)
        ws2 = await svc2.get_or_provision(NotBlankStr("proj-1"))

        assert ws2.git_backend_kind is GitBackendType.LOCAL_PATH
        local.provision.assert_awaited_once()  # type: ignore[attr-defined]

    async def test_concurrent_first_touch_provisions_once(self, tmp_path: Path) -> None:
        repo = _FakeWorkspaceRepo()
        backend = _git_backend()
        svc = _service(tmp_path, repo, backend)

        a, b = await asyncio.gather(
            svc.get_or_provision(NotBlankStr("proj-1")),
            svc.get_or_provision(NotBlankStr("proj-1")),
        )

        assert a == b
        backend.provision.assert_awaited_once()  # type: ignore[attr-defined]
        assert repo.save_calls == 1
