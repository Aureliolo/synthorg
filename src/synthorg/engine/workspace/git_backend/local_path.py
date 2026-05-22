"""Local-path git backend: bring-your-own repository base on disk.

The configured ``local_repo_path`` is treated as a BASE directory under
which one repository per project is provisioned at
``<local_repo_path>/<project_id>``; this is what guarantees per-project
isolation when the local-path backend is paired with the multi-project
:class:`~synthorg.engine.workspace.project_workspace_service.ProjectWorkspaceService`.
There is no separate remote: the on-disk repo is the durable store, so
``push``/``fetch`` resolve against the repo itself (the coordinator
merge queue still serialises merges upstream).
"""

import asyncio
from pathlib import Path

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import GitBackendType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    GitBackendConfigError,
    GitBackendProvisionError,
    GitBackendPushError,
)
from synthorg.engine.workspace.git_backend._git_ops import (
    assert_standalone_repo,
    git,
    import_source_into_worktree,
    is_git_repo,
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
    GIT_BACKEND_PROVISION_COMPLETE,
    GIT_BACKEND_PROVISION_START,
    GIT_BACKEND_PUSH_COMPLETE,
    GIT_BACKEND_PUSH_FAILED,
    GIT_BACKEND_SEED_COMPLETE,
    GIT_BACKEND_SEED_START,
)

logger = get_logger(__name__)

_BOT_NAME = "SynthOrg"
_BOT_EMAIL = "synthorg-bot@synthorg.local"


class LocalPathGitBackend:
    """Caller-supplied local git repository backend.

    Each project gets its own repository at ``<local_repo_path>/<project_id>``;
    the configured ``local_repo_path`` is the BASE under which those
    per-project repos live, NOT a single shared working tree.
    """

    def __init__(
        self,
        *,
        local_repo_path: str,
        cmd_timeout: float,
        clock: Clock | None = None,
    ) -> None:
        self._repo_base = Path(local_repo_path)
        self._cmd_timeout = cmd_timeout
        self._clock: Clock = clock if clock is not None else SystemClock()

    def get_backend_type(self) -> GitBackendType:
        """Return the ``LOCAL_PATH`` discriminator."""
        return GitBackendType.LOCAL_PATH

    def _repo_path_for_project(self, project_id: str) -> Path:
        """Derive the per-project repository path under the base."""
        return self._repo_base / project_id

    def _non_empty_non_repo_dir(self, repo_path: Path) -> bool:
        """True if *repo_path* exists with content (sync; run off-loop).

        A path that exists as a file (not a directory) also counts as
        "non-empty" -- ``iterdir()`` on a file raises ``NotADirectoryError``,
        and the caller's intent is to refuse provisioning over a
        non-empty location regardless of whether it is a file or a dir.
        """
        if not repo_path.exists():
            return False
        if not repo_path.is_dir():
            return True
        return any(repo_path.iterdir())

    async def _reject_if_nested_in_parent_worktree(self, path: Path, pid: str) -> None:
        """Refuse if *path* is inside an existing parent working tree.

        ``git rev-parse --show-toplevel`` succeeds (exit 0) and reports a
        toplevel DIFFERENT from *path* iff some parent dir of *path* is a
        working tree; in that case ``git init`` would silently no-op and
        the subsequent ``git config`` / ``git commit`` would mutate the
        outer repo. Raise instead of silently corrupting it.
        """
        from synthorg.engine.workspace._git_subprocess import (  # noqa: PLC0415
            run_git_subprocess,
        )
        from synthorg.observability.events.workspace import (  # noqa: PLC0415
            GIT_BACKEND_PROVISION_FAILED,
        )

        rc, stdout, _stderr = await run_git_subprocess(
            path,
            "rev-parse",
            "--show-toplevel",
            cmd_timeout=self._cmd_timeout,
            log_event=GIT_BACKEND_PROVISION_FAILED,
        )
        if rc != 0:
            return
        toplevel = await asyncio.to_thread(Path(stdout).resolve)
        if toplevel == await asyncio.to_thread(path.resolve):
            return
        msg = (
            f"refusing to provision project {pid!r}: local repo path "
            f"{path!s} is nested inside an existing parent working tree "
            f"at {toplevel!s}"
        )
        raise GitBackendProvisionError(msg)

    async def provision(
        self,
        *,
        project_id: NotBlankStr,
        workspace_path: Path,  # noqa: ARG002 -- per-project base path derived from config
        default_branch: NotBlankStr,
    ) -> ProvisionResult:
        """Validate / initialise the per-project local repository."""
        pid = str(project_id)
        repo_path = self._repo_path_for_project(pid)
        logger.info(
            GIT_BACKEND_PROVISION_START,
            project_id=pid,
            backend=GitBackendType.LOCAL_PATH.value,
        )
        if await is_git_repo(repo_path, cmd_timeout=self._cmd_timeout):
            return ProvisionResult(
                repo_root=NotBlankStr(str(repo_path)),
                default_branch=default_branch,
                newly_created=False,
            )
        if await asyncio.to_thread(self._non_empty_non_repo_dir, repo_path):
            msg = (
                f"local repo path {repo_path!s} exists but is not a "
                "git repository and is not empty"
            )
            raise GitBackendConfigError(msg)
        try:
            await asyncio.to_thread(repo_path.mkdir, parents=True, exist_ok=True)
        except OSError as exc:
            msg = f"failed to create local repo dir for {pid!r}"
            raise GitBackendProvisionError(msg) from exc
        # Refuse to run ``git init`` / ``git config`` / ``git commit`` if the
        # caller-supplied path is nested inside a parent working tree (e.g.
        # accidentally pointing at a subdir of the synthorg repo). Without
        # this guard the subsequent commands would mutate that outer repo's
        # config + add stray empty commits to it.
        await self._reject_if_nested_in_parent_worktree(repo_path, pid)
        await git(
            repo_path,
            "init",
            "--initial-branch",
            str(default_branch),
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        # Refuse if repo_path is a worktree / shared-config repo. Without
        # this guard, ``git config user.{email,name}`` writes here would
        # mutate the parent repo's shared config and silently rewrite the
        # operator's identity across every other linked worktree.
        await assert_standalone_repo(
            repo_path,
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        await git(
            repo_path,
            "config",
            "user.email",
            _BOT_EMAIL,
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        await git(
            repo_path,
            "config",
            "user.name",
            _BOT_NAME,
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        await git(
            repo_path,
            "commit",
            "--allow-empty",
            "-m",
            "Initialise project workspace",
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        logger.info(
            GIT_BACKEND_PROVISION_COMPLETE,
            project_id=pid,
            backend=GitBackendType.LOCAL_PATH.value,
        )
        return ProvisionResult(
            repo_root=NotBlankStr(str(repo_path)),
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
        """Import *source* into the per-project repo (on-disk durable store)."""
        pid = str(project_id)
        logger.info(
            GIT_BACKEND_SEED_START,
            project_id=pid,
            backend=GitBackendType.LOCAL_PATH.value,
            source_kind=source.source_kind.value,
        )
        await import_source_into_worktree(
            repo_root,
            source=source,
            cmd_timeout=self._cmd_timeout,
            project_id=pid,
        )
        pushed = await self.push(
            project_id=project_id,
            repo_root=repo_root,
            branch=default_branch,
            base_branch=default_branch,
        )
        logger.info(
            GIT_BACKEND_SEED_COMPLETE,
            project_id=pid,
            backend=GitBackendType.LOCAL_PATH.value,
        )
        return SeedResult(
            repo_root=NotBlankStr(str(repo_root)),
            default_branch=default_branch,
            head_sha=pushed.head_sha,
            source_kind=source.source_kind,
        )

    async def push(
        self,
        *,
        project_id: NotBlankStr,
        repo_root: Path,
        branch: NotBlankStr,
        base_branch: NotBlankStr,  # noqa: ARG002 -- no remote; on-disk is durable
    ) -> PushResult:
        """Resolve the branch head (on-disk repo is the durable store)."""
        # Local-path: the on-disk repo IS the durable store; "push" is
        # the no-op durability point. Resolve the branch head so callers
        # still get a verifiable commit SHA.
        pid = str(project_id)
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
        project_id: NotBlankStr,  # noqa: ARG002 -- no remote to fetch from
        repo_root: Path,  # noqa: ARG002
        branch: NotBlankStr | None = None,  # noqa: ARG002
    ) -> FetchResult:
        """No remote to fetch from; returns empty refs for protocol parity."""
        # No remote: nothing to fetch. Returning empty refs keeps the
        # protocol contract uniform across backends.
        return FetchResult(updated_refs=())


__all__ = ["LocalPathGitBackend"]
