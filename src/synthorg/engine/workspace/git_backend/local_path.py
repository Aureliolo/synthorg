"""Local-path git backend: bring-your-own repository on disk.

The configured ``local_repo_path`` is authoritative and IS the project
working tree.  There is no separate remote: the on-disk repo is the
durable store, so ``push``/``fetch`` resolve against the repo itself
(the coordinator merge queue still serialises merges upstream).
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
from synthorg.engine.workspace.git_backend._git_ops import git, is_git_repo
from synthorg.engine.workspace.git_backend.protocol import (
    FetchResult,
    ProvisionResult,
    PushResult,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    GIT_BACKEND_PROVISION_COMPLETE,
    GIT_BACKEND_PROVISION_START,
    GIT_BACKEND_PUSH_COMPLETE,
    GIT_BACKEND_PUSH_FAILED,
)

logger = get_logger(__name__)

_BOT_NAME = "SynthOrg"
_BOT_EMAIL = "synthorg-bot@synthorg.local"


class LocalPathGitBackend:
    """Caller-supplied local git repository backend."""

    def __init__(
        self,
        *,
        local_repo_path: str,
        cmd_timeout: float,
        clock: Clock | None = None,
    ) -> None:
        self._repo_path = Path(local_repo_path)
        self._cmd_timeout = cmd_timeout
        self._clock: Clock = clock if clock is not None else SystemClock()

    def get_backend_type(self) -> GitBackendType:
        """Return the ``LOCAL_PATH`` discriminator."""
        return GitBackendType.LOCAL_PATH

    def _non_empty_non_repo_dir(self) -> bool:
        """True if the path exists with content (sync; run off-loop)."""
        return self._repo_path.exists() and any(self._repo_path.iterdir())

    async def provision(
        self,
        *,
        project_id: NotBlankStr,
        workspace_path: Path,  # noqa: ARG002 -- local repo path is authoritative
        default_branch: NotBlankStr,
    ) -> ProvisionResult:
        """Validate / initialise the caller-supplied local repo."""
        pid = str(project_id)
        logger.info(
            GIT_BACKEND_PROVISION_START,
            project_id=pid,
            backend=GitBackendType.LOCAL_PATH.value,
        )
        if await is_git_repo(self._repo_path, cmd_timeout=self._cmd_timeout):
            return ProvisionResult(
                repo_root=NotBlankStr(str(self._repo_path)),
                default_branch=default_branch,
                newly_created=False,
            )
        if await asyncio.to_thread(self._non_empty_non_repo_dir):
            msg = (
                f"local_repo_path {self._repo_path!s} exists but is not a "
                "git repository and is not empty"
            )
            raise GitBackendConfigError(msg)
        try:
            await asyncio.to_thread(self._repo_path.mkdir, parents=True, exist_ok=True)
        except OSError as exc:
            msg = f"failed to create local repo dir for {pid!r}"
            raise GitBackendProvisionError(msg) from exc
        await git(
            self._repo_path,
            "init",
            "--initial-branch",
            str(default_branch),
            ".",
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        # Defensive: refuse to run ``git config`` / ``git commit`` if init
        # did not create a local ``.git``; otherwise the commands would
        # silently walk up to a parent working tree.
        if not await asyncio.to_thread((self._repo_path / ".git").exists):
            msg = (
                f"git init did not create .git for project {pid!r} at "
                f"{self._repo_path!s}; refusing to run git config/commit"
            )
            raise GitBackendProvisionError(msg)
        await git(
            self._repo_path,
            "config",
            "user.email",
            _BOT_EMAIL,
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        await git(
            self._repo_path,
            "config",
            "user.name",
            _BOT_NAME,
            cmd_timeout=self._cmd_timeout,
            fail_exc=GitBackendProvisionError,
            project_id=pid,
        )
        await git(
            self._repo_path,
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
            repo_root=NotBlankStr(str(self._repo_path)),
            default_branch=default_branch,
            newly_created=True,
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
