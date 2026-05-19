"""Embedded git backend: self-hosted bare repo on the persistent volume.

The safe default.  Each project gets a bare repository at
``<base_root>/<embedded_subdir>/<project_id>.git`` and a working tree
at the project workspace path with ``origin`` pointing at that bare
repo.  No external dependency; pushes/fetches are pure-local.
"""

import asyncio
from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649)
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import GitBackendType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    GitBackendFetchError,
    GitBackendProvisionError,
    GitBackendPushError,
)
from synthorg.engine.workspace.git_backend._git_ops import git, is_git_repo
from synthorg.engine.workspace.git_backend.protocol import (
    FetchResult,
    ProvisionResult,
    PushResult,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    GIT_BACKEND_FETCH_COMPLETE,
    GIT_BACKEND_FETCH_FAILED,
    GIT_BACKEND_PROVISION_COMPLETE,
    GIT_BACKEND_PROVISION_START,
    GIT_BACKEND_PUSH_COMPLETE,
    GIT_BACKEND_PUSH_FAILED,
)

logger = get_logger(__name__)

_BOT_NAME: Final[str] = "SynthOrg"
_BOT_EMAIL: Final[str] = "synthorg-bot@synthorg.local"
_REMOTE_NAME: Final[str] = "origin"


class EmbeddedGitBackend:
    """Bare-repo-on-volume git backend (default strategy)."""

    def __init__(
        self,
        *,
        base_root: Path,
        embedded_subdir: str,
        cmd_timeout: float,
        clock: Clock | None = None,
    ) -> None:
        self._base_root = base_root
        self._embedded_subdir = embedded_subdir
        self._cmd_timeout = cmd_timeout
        self._clock: Clock = clock if clock is not None else SystemClock()

    def get_backend_type(self) -> GitBackendType:
        """Return the ``EMBEDDED`` discriminator."""
        return GitBackendType.EMBEDDED

    def _bare_repo_path(self, project_id: str) -> Path:
        return self._base_root / self._embedded_subdir / f"{project_id}.git"

    async def _reject_if_nested_in_parent_worktree(self, path: Path, pid: str) -> None:
        """Refuse to provision inside an existing parent working tree.

        ``rev-parse --show-toplevel`` succeeds (exit 0) iff *path* is inside
        some git working tree. After ``mkdir`` of a fresh workspace dir, if
        this succeeds the parent is a working tree and provisioning would
        mutate the wrong repo. Raise instead of silently corrupting it.
        """
        from synthorg.engine.workspace._git_subprocess import (  # noqa: PLC0415
            run_git_subprocess,
        )
        from synthorg.observability.events.workspace import (  # noqa: PLC0415
            GIT_BACKEND_PROVISION_FAILED,
        )

        rc, _stdout, _stderr = await run_git_subprocess(
            path,
            "rev-parse",
            "--show-toplevel",
            cmd_timeout=self._cmd_timeout,
            log_event=GIT_BACKEND_PROVISION_FAILED,
        )
        if rc == 0:
            msg = (
                f"refusing to provision project {pid!r} inside an existing "
                f"parent git working tree at {path!s}"
            )
            raise GitBackendProvisionError(msg)

    async def _configure_identity(self, workspace_path: Path, pid: str) -> None:
        await git(
            workspace_path,
            "config",
            "user.email",
            _BOT_EMAIL,
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        await git(
            workspace_path,
            "config",
            "user.name",
            _BOT_NAME,
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )

    async def provision(
        self,
        *,
        project_id: NotBlankStr,
        workspace_path: Path,
        default_branch: NotBlankStr,
    ) -> ProvisionResult:
        """Create the bare repo + working tree (idempotent)."""
        pid = str(project_id)
        logger.info(
            GIT_BACKEND_PROVISION_START,
            project_id=pid,
            backend=GitBackendType.EMBEDDED.value,
        )
        if await is_git_repo(workspace_path, cmd_timeout=self._cmd_timeout):
            return ProvisionResult(
                repo_root=NotBlankStr(str(workspace_path)),
                default_branch=default_branch,
                newly_created=False,
            )

        bare = self._bare_repo_path(pid)
        try:
            await asyncio.to_thread(bare.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(workspace_path.mkdir, parents=True, exist_ok=True)
        except OSError as exc:
            msg = f"failed to create workspace dirs for {pid!r}"
            raise GitBackendProvisionError(msg) from exc
        # Safety: never run ``git init`` / ``git config`` / ``git commit`` from
        # inside an existing parent working tree. ``is_git_repo`` already
        # short-circuits when *workspace_path* itself is a working tree, but
        # if a parent dir is a working tree (e.g. the synthorg repo when
        # base_root was misconfigured) the subsequent commands would mutate
        # that outer repo's config and create stray empty commits on it.
        await self._reject_if_nested_in_parent_worktree(bare, pid)
        await self._reject_if_nested_in_parent_worktree(workspace_path, pid)

        await git(
            bare,
            "init",
            "--bare",
            "--initial-branch",
            str(default_branch),
            ".",
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        await git(
            workspace_path,
            "init",
            "--initial-branch",
            str(default_branch),
            ".",
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        # Defensive: ``git init`` must have created a local ``.git`` (dir or
        # the worktree-link file). Otherwise a subsequent ``git config`` /
        # ``git commit`` would silently walk up to a parent working tree and
        # corrupt its config + add stray empty commits.
        if not await asyncio.to_thread((workspace_path / ".git").exists):
            msg = (
                f"git init did not create .git for project {pid!r} at "
                f"{workspace_path!s}; refusing to run git config/commit"
            )
            raise GitBackendProvisionError(msg)
        await self._configure_identity(workspace_path, pid)
        await git(
            workspace_path,
            "commit",
            "--allow-empty",
            "-m",
            "Initialise project workspace",
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        await git(
            workspace_path,
            "remote",
            "add",
            _REMOTE_NAME,
            str(bare),
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        await git(
            workspace_path,
            "push",
            _REMOTE_NAME,
            str(default_branch),
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        logger.info(
            GIT_BACKEND_PROVISION_COMPLETE,
            project_id=pid,
            backend=GitBackendType.EMBEDDED.value,
        )
        return ProvisionResult(
            repo_root=NotBlankStr(str(workspace_path)),
            default_branch=default_branch,
            newly_created=True,
        )

    async def push(
        self,
        *,
        project_id: NotBlankStr,
        repo_root: Path,
        branch: NotBlankStr,
        base_branch: NotBlankStr,  # noqa: ARG002 -- local origin tracks base
    ) -> PushResult:
        """Push *branch* to the project's bare repo; return its head SHA."""
        pid = str(project_id)
        await git(
            repo_root,
            "push",
            _REMOTE_NAME,
            str(branch),
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendPushError,
            project_id=pid,
            event=GIT_BACKEND_PUSH_FAILED,
        )
        head = await git(
            repo_root,
            "rev-parse",
            str(branch),
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendPushError,
            project_id=pid,
            event=GIT_BACKEND_PUSH_FAILED,
        )
        logger.info(GIT_BACKEND_PUSH_COMPLETE, project_id=pid, branch=str(branch))
        return PushResult(branch=branch, head_sha=NotBlankStr(head))

    async def fetch(
        self,
        *,
        project_id: NotBlankStr,
        repo_root: Path,
        branch: NotBlankStr | None = None,
    ) -> FetchResult:
        """Fetch from the project's bare repo into *repo_root*."""
        pid = str(project_id)
        args = ["fetch", _REMOTE_NAME]
        if branch is not None:
            args.append(str(branch))
        await git(
            repo_root,
            *args,
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendFetchError,
            project_id=pid,
            event=GIT_BACKEND_FETCH_FAILED,
        )
        logger.info(GIT_BACKEND_FETCH_COMPLETE, project_id=pid)
        refs: tuple[NotBlankStr, ...] = (
            (NotBlankStr(str(branch)),) if branch is not None else ()
        )
        return FetchResult(updated_refs=refs)


__all__ = ["EmbeddedGitBackend"]
