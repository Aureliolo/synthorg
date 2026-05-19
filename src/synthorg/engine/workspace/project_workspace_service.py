"""Persistent per-project workspace provisioning service.

Resolves (and lazily provisions, once) the 1:1 persistent git-backed
workspace for a project.  ``GitBackendConfig.kind`` is authoritative:
if a persisted row was provisioned under a different backend than the
live config, the workspace is re-provisioned under the new backend and
the row updated (the on-disk working tree path is preserved).
"""

import asyncio
from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649)
from typing import TYPE_CHECKING, Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import GitBackendType
from synthorg.core.project_workspace import ProjectWorkspace
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    PROJECT_WORKSPACE_PROVISIONED,
    PROJECT_WORKSPACE_REUSED,
    WORKSPACE_BACKEND_KIND_CHANGED,
)

if TYPE_CHECKING:
    from synthorg.engine.workspace.git_backend import (
        GitBackend,
        GitBackendConfig,
    )
    from synthorg.persistence.project_workspace_protocol import (
        ProjectWorkspaceRepository,
    )

logger = get_logger(__name__)

_PROJECTS_SUBDIR: Final[str] = "projects"
_DEFAULT_BRANCH: NotBlankStr = NotBlankStr("main")


class ProjectWorkspaceService:
    """Provisions and resolves the persistent workspace for a project.

    Args:
        base_root: Persistent volume base; project trees live under
            ``<base_root>/projects/<project_id>``.
        repo: Persistence for the :class:`ProjectWorkspace` row.
        git_backend: The configured backend that provisions the repo.
        config: Git-backend config (its ``kind`` is authoritative).
        clock: Clock seam for row timestamps.
    """

    __slots__ = (
        "_base_root",
        "_clock",
        "_config",
        "_git_backend",
        "_locks",
        "_locks_guard",
        "_repo",
    )

    def __init__(
        self,
        *,
        base_root: Path,
        repo: ProjectWorkspaceRepository,
        git_backend: GitBackend,
        config: GitBackendConfig,
        clock: Clock | None = None,
    ) -> None:
        self._base_root = base_root
        self._repo = repo
        self._git_backend = git_backend
        self._config = config
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    @property
    def git_backend(self) -> GitBackend:
        """The wired git backend; consumed by downstream wiring."""
        return self._git_backend

    def _workspace_path(self, project_id: str) -> Path:
        return self._base_root / _PROJECTS_SUBDIR / project_id

    async def _lock_for(self, project_id: str) -> asyncio.Lock:
        """Return the per-project provisioning lock (created once)."""
        existing = self._locks.get(project_id)
        if existing is not None:
            return existing
        async with self._locks_guard:
            return self._locks.setdefault(project_id, asyncio.Lock())

    async def get_or_provision(
        self,
        project_id: NotBlankStr,
    ) -> ProjectWorkspace:
        """Return the project's workspace, provisioning it once if absent.

        Concurrent first-touch by two agents provisions exactly once
        (per-project lock).  A persisted row whose backend kind differs
        from the live config is re-provisioned under the new backend.

        Returns:
            The persisted :class:`ProjectWorkspace`.

        Raises:
            GitBackendProvisionError: Repository provisioning failed.
        """
        lock = await self._lock_for(project_id)
        async with lock:
            row = await self._repo.get(project_id)
            kind = self._config.kind
            if row is not None and row.git_backend_kind == kind:
                logger.info(
                    PROJECT_WORKSPACE_REUSED,
                    project_id=project_id,
                    backend=kind.value,
                )
                return row
            if row is not None and row.git_backend_kind != kind:
                logger.warning(
                    WORKSPACE_BACKEND_KIND_CHANGED,
                    project_id=project_id,
                    from_backend=row.git_backend_kind.value,
                    to_backend=kind.value,
                )
            return await self._provision(project_id, row, kind)

    async def _provision(
        self,
        project_id: NotBlankStr,
        prior: ProjectWorkspace | None,
        kind: GitBackendType,
    ) -> ProjectWorkspace:
        """Provision (or re-provision) and persist the workspace row."""
        workspace_path = self._workspace_path(project_id)
        await asyncio.to_thread(workspace_path.mkdir, parents=True, exist_ok=True)
        default_branch = prior.default_branch if prior is not None else _DEFAULT_BRANCH
        result = await self._git_backend.provision(
            project_id=project_id,
            workspace_path=workspace_path,
            default_branch=default_branch,
        )
        now = self._clock.now()
        remote_ref = (
            NotBlankStr(self._config.remote_connection_name)
            if (
                kind is GitBackendType.EXTERNAL_REMOTE
                and self._config.remote_connection_name
            )
            else None
        )
        workspace = ProjectWorkspace(
            project_id=project_id,
            workspace_path=result.repo_root,
            git_backend_kind=kind,
            remote_ref=remote_ref,
            default_branch=result.default_branch,
            created_at=prior.created_at if prior is not None else now,
            updated_at=now,
        )
        await self._repo.save(workspace)
        logger.info(
            PROJECT_WORKSPACE_PROVISIONED,
            project_id=project_id,
            backend=kind.value,
            newly_created=result.newly_created,
        )
        return workspace


__all__ = ["ProjectWorkspaceService"]
