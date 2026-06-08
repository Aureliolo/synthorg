"""Persistent per-project workspace provisioning service.

Resolves (and lazily provisions, once) the 1:1 persistent git-backed
workspace for a project.  ``GitBackendConfig.kind`` is authoritative:
if a persisted row was provisioned under a different backend than the
live config, the workspace is re-provisioned under the new backend.
On a kind switch the prior backend's ``.git`` directory at the prior
on-disk path is removed before the new backend provisions, so each
backend's ``is_git_repo`` short-circuit cannot keep the old layout
alive after the row claims the new kind; the new backend then decides
its own on-disk location (the persisted row reflects whatever path
the new backend reports).
"""

import asyncio
import shutil
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.project_enums import GitBackendType
from synthorg.core.project_workspace import ProjectWorkspace
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import GitBackendConfigError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workspace import (
    PROJECT_WORKSPACE_PROVISIONED,
    PROJECT_WORKSPACE_REUSED,
    WORKSPACE_BACKEND_KIND_CHANGED,
    WORKSPACE_GIT_DIR_CLEARED,
    WORKSPACE_PATH_TRAVERSAL_REJECTED,
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


def _force_writable_then_retry(
    func: Callable[[str], object],
    path: str,
    exc: BaseException,
) -> None:
    """``shutil.rmtree`` ``onexc`` handler: strip read-only, retry once.

    Git pack-object files under ``.git/objects`` are written read-only
    on Windows. ``shutil.rmtree`` raises ``PermissionError`` rather than
    stripping the attribute, which would leave an orphan ``.git`` tree
    behind after a backend kind switch. The next backend's
    ``is_git_repo`` short-circuit would then keep the old layout alive
    despite the persisted row claiming the new kind.

    The chmod ORs the write bit into the existing mode (rather than
    replacing it) so read + execute bits are preserved; without them a
    directory entry would lose traversability mid-walk. ``lstat`` +
    ``follow_symlinks=False`` keep the operation pinned to the named
    entry instead of any symlink target a third-party repo planted.
    """
    if not isinstance(exc, PermissionError):
        raise exc
    try:
        current_mode = Path(path).lstat().st_mode
        Path(path).chmod(current_mode | stat.S_IWRITE, follow_symlinks=False)
    except OSError:
        raise exc from None
    func(path)


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
        # Defense-in-depth: ``project_id`` is system-generated and reaches
        # this seam only via persisted Project rows, but rejecting path
        # separators here closes the door if a future caller ever passes
        # an attacker-controlled value (no traversal out of the projects
        # subdir, no absolute-path takeover of the base root).
        if "/" in project_id or "\\" in project_id or ".." in project_id:
            # Log before raising so blocked traversal attempts surface
            # in production audit logs, not just on the caller's stack.
            logger.warning(
                WORKSPACE_PATH_TRAVERSAL_REJECTED,
                project_id=project_id,
                base_root=str(self._base_root),
                projects_subdir=_PROJECTS_SUBDIR,
            )
            msg = (
                f"refusing path-separator-bearing project_id "
                f"{project_id!r}: workspace path traversal blocked"
            )
            raise GitBackendConfigError(msg)
        return self._base_root / _PROJECTS_SUBDIR / project_id

    async def _clear_prior_git_dir(
        self,
        *,
        project_id: str,
        prior_path: Path,
    ) -> None:
        """Remove the prior backend's ``.git`` directory before a kind switch.

        Every backend short-circuits ``provision()`` when
        ``is_git_repo(path)`` is true; without clearing the prior
        ``.git`` metadata on a kind change, the new backend (EMBEDDED ->
        EXTERNAL_REMOTE, etc.) would never re-initialise the on-disk
        layout despite the row claiming the new kind. Only the ``.git``
        subdirectory is removed; any user-owned files at ``prior_path``
        (e.g. a BYO LOCAL_PATH tree) are left untouched.
        """
        git_dir = prior_path / ".git"
        if not await asyncio.to_thread(git_dir.exists):
            return
        try:
            await asyncio.to_thread(
                shutil.rmtree, git_dir, onexc=_force_writable_then_retry
            )
        except OSError as exc:
            # Best-effort: surface the failure but let the new
            # backend's provision report whatever it sees on disk.
            logger.warning(
                WORKSPACE_GIT_DIR_CLEARED,
                project_id=project_id,
                git_dir=str(git_dir),
                success=False,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return
        logger.info(
            WORKSPACE_GIT_DIR_CLEARED,
            project_id=project_id,
            git_dir=str(git_dir),
            success=True,
        )

    async def _lock_for(self, project_id: str) -> asyncio.Lock:
        """Return the per-project provisioning lock (created once)."""
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
        """Provision (or re-provision) and persist the workspace row.

        Returns:
            The newly persisted :class:`ProjectWorkspace` row
            describing the per-project workspace.
        """
        # Reuse the persisted on-disk location across re-provisions: this
        # avoids relocating the tree if ``_workspace_path()`` ever moves
        # in a future refactor, and on a same-kind re-provision keeps the
        # path deterministically equal to the prior one.
        workspace_path = (
            Path(prior.workspace_path)
            if prior is not None
            else self._workspace_path(project_id)
        )
        await asyncio.to_thread(workspace_path.mkdir, parents=True, exist_ok=True)
        if prior is not None and prior.git_backend_kind != kind:
            # Kind switch: remove the prior backend's ``.git`` metadata
            # so the new backend's ``is_git_repo`` short-circuit cannot
            # keep the old layout alive (otherwise EMBEDDED -> EXTERNAL_REMOTE
            # would never clone, EMBEDDED -> LOCAL_PATH would never reinit,
            # etc., and acceptance #3 stays dead on disk).
            await self._clear_prior_git_dir(
                project_id=project_id,
                prior_path=Path(prior.workspace_path),
            )
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
