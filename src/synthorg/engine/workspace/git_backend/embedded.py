"""Embedded git backend: self-hosted bare repo on the persistent volume.

The safe default.  Each project gets a bare repository at
``<base_root>/<embedded_subdir>/<project_id>.git`` and a working tree
at the project workspace path with ``origin`` pointing at that bare
repo.  No external dependency; pushes/fetches are pure-local.
"""

import asyncio
from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649)

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import GitBackendType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    GitBackendFetchError,
    GitBackendProvisionError,
    GitBackendPushError,
    GitBackendSeedError,
)
from synthorg.engine.workspace.git_backend._git_ops import (
    REMOTE_NAME,
    configure_identity,
    git,
    import_source_into_worktree,
    is_git_repo,
    reject_if_nested_in_parent_worktree,
)
from synthorg.engine.workspace.git_backend.protocol import (
    FetchResult,
    ProvisionResult,
    PushResult,
    ResolvedSource,
    SeedResult,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    GIT_BACKEND_FETCH_COMPLETE,
    GIT_BACKEND_FETCH_FAILED,
    GIT_BACKEND_PROVISION_COMPLETE,
    GIT_BACKEND_PROVISION_START,
    GIT_BACKEND_PUSH_COMPLETE,
    GIT_BACKEND_PUSH_FAILED,
    GIT_BACKEND_SEED_COMPLETE,
    GIT_BACKEND_SEED_FAILED,
    GIT_BACKEND_SEED_START,
)

logger = get_logger(__name__)


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
        # Safety: never run ``git init`` / ``git config`` / ``git commit``
        # from inside an existing parent working tree. ``is_git_repo``
        # already short-circuits when *workspace_path* itself is a working
        # tree, but if a parent dir is a working tree (e.g. the synthorg
        # repo when base_root was misconfigured) the subsequent commands
        # would mutate that outer repo's config and create stray empty
        # commits on it. The bare path is created under our own
        # config-controlled ``base_root`` so we only guard the working
        # tree, which is where ``git config`` / ``git commit`` execute.
        await reject_if_nested_in_parent_worktree(
            workspace_path,
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )

        await git(
            bare,
            "init",
            "--bare",
            "--initial-branch",
            str(default_branch),
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        await git(
            workspace_path,
            "init",
            "--initial-branch",
            str(default_branch),
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        await configure_identity(
            workspace_path,
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
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
            REMOTE_NAME,
            str(bare),
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        await git(
            workspace_path,
            "push",
            REMOTE_NAME,
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

    async def seed(
        self,
        *,
        project_id: NotBlankStr,
        repo_root: Path,
        source: ResolvedSource,
        default_branch: NotBlankStr,
    ) -> SeedResult:
        """Import *source* into the working tree, then push to the bare repo."""
        pid = str(project_id)
        logger.info(
            GIT_BACKEND_SEED_START,
            project_id=pid,
            backend=GitBackendType.EMBEDDED.value,
            source_kind=source.source_kind.value,
        )
        await import_source_into_worktree(
            repo_root,
            source=source,
            cmd_timeout=self._cmd_timeout,
            project_id=pid,
        )
        # Provisioning pushed an empty initial commit to the bare repo;
        # the imported history is unrelated to it, so a plain push is a
        # non-fast-forward rejection. Force-update the bare repo: the only
        # thing overwritten is that throwaway empty commit on a workspace
        # that was provisioned moments ago for this very import.
        await git(
            repo_root,
            "push",
            "--force",
            REMOTE_NAME,
            str(default_branch),
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendSeedError,
            project_id=pid,
            event=GIT_BACKEND_SEED_FAILED,
        )
        head = await git(
            repo_root,
            "rev-parse",
            str(default_branch),
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendSeedError,
            project_id=pid,
            event=GIT_BACKEND_SEED_FAILED,
        )
        logger.info(
            GIT_BACKEND_SEED_COMPLETE,
            project_id=pid,
            backend=GitBackendType.EMBEDDED.value,
        )
        return SeedResult(
            repo_root=NotBlankStr(str(repo_root)),
            default_branch=default_branch,
            head_sha=NotBlankStr(head),
            source_kind=source.source_kind,
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
            REMOTE_NAME,
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
        args = ["fetch", REMOTE_NAME]
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
