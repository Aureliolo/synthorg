"""Embedded git backend: self-hosted bare repo on the persistent volume.

The safe default.  Each project gets a bare repository at
``<base_root>/<embedded_subdir>/<project_id>.git`` and a working tree
at the project workspace path with ``origin`` pointing at that bare
repo.  No external dependency; pushes/fetches are pure-local.
"""

import asyncio
from pathlib import Path
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.project_enums import GitBackendType
from synthorg.core.resilience import GeneralRetryHandler
from synthorg.core.types import NotBlankStr
from synthorg.core.workspace_sharing import ensure_shared_dir
from synthorg.engine.errors import (
    GitBackendFetchError,
    GitBackendProvisionError,
    GitBackendPushError,
    GitBackendSeedError,
)
from synthorg.engine.workspace._git_subprocess import (
    GIT_RC_SPAWN_FAILED,
    GIT_RC_TIMED_OUT,
    git_failure_detail,
)
from synthorg.engine.workspace.git_backend._git_ops import (
    REMOTE_NAME,
    configure_identity,
    git,
    import_source_into_worktree,
    is_git_repo,
    reject_if_nested_in_parent_worktree,
)
from synthorg.engine.workspace.git_backend._ref_transfer import (
    GitFailure,
    transfer_ref_local,
)
from synthorg.engine.workspace.git_backend.config import GitBackendResilienceConfig
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
    GIT_BACKEND_PROVISION_FAILED,
    GIT_BACKEND_PROVISION_START,
    GIT_BACKEND_PUSH_COMPLETE,
    GIT_BACKEND_PUSH_FAILED,
    GIT_BACKEND_PUSH_RETRY,
    GIT_BACKEND_SEED_COMPLETE,
    GIT_BACKEND_SEED_FAILED,
    GIT_BACKEND_SEED_START,
)

logger = get_logger(__name__)

#: A working tree's git directory, the target a bundle fetch addresses when
#: the transfer runs the other way (bare repo into the clone).
_GIT_DIR: Final[str] = ".git"


#: What git says when it lost a race for a lock: the ref lock by name, and
#: every ``*.lock`` collision (index, packed-refs) by the phrase that closes
#: git's message. This is the condition the retry exists for, and it clears
#: when whichever writer holds the lock finishes.
_CONTENTION_MARKERS: Final[tuple[str, ...]] = (
    "cannot lock ref",
    "file exists",
    "resource temporarily unavailable",
)

#: Sentinels for a command that reached no verdict, so there is none to
#: repeat. Read from the renderer rather than copied, so a reworded
#: description cannot leave a marker matching nothing. The two absent
#: sentinels are deliberate: no wait puts git on PATH, and none creates a
#: repository directory that does not exist.
_INCOMPLETE_MARKERS: Final[tuple[str, ...]] = (
    git_failure_detail(GIT_RC_TIMED_OUT).lower(),
    git_failure_detail(GIT_RC_SPAWN_FAILED).lower(),
)


def _is_retryable_local_git_op(exc: Exception) -> bool:
    """Predicate for the transient-I/O retry handler.

    Push and fetch only. Provisioning and seeding are excluded on purpose:
    both are one-shot imports onto a tree a failed attempt may have
    half-written, so a second run recovers into a different and worse state
    than the one it was recovering from.

    Even among those two, the operation being retryable is not the question.
    Both ends of a bundle transfer are local, so a rejected update is a
    verdict git reaches again from the same two repositories: retrying spends
    the whole backoff budget on the agent's critical path and then reports
    what the first attempt already knew. The sibling external-remote backend
    draws the same line, refusing an auth failure and a missing remote by
    name, and it is the line the retry-pattern reference states: a permanent
    failure surfaces at once.

    Returns:
        ``True`` when git lost a lock race or never reached a verdict.
    """
    if not isinstance(exc, GitBackendPushError | GitBackendFetchError):
        return False
    reported = str(exc).lower()
    return any(
        marker in reported for marker in _CONTENTION_MARKERS + _INCOMPLETE_MARKERS
    )


class EmbeddedGitBackend:
    """Bare-repo-on-volume git backend (default strategy)."""

    def __init__(
        self,
        *,
        base_root: Path,
        embedded_subdir: str,
        cmd_timeout: float,
        resilience: GitBackendResilienceConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._base_root = base_root
        self._embedded_subdir = embedded_subdir
        self._cmd_timeout = cmd_timeout
        self._clock: Clock = clock if clock is not None else SystemClock()
        cfg = resilience if resilience is not None else GitBackendResilienceConfig()
        # Local does not mean reliable. A bare repo on a shared volume loses a
        # race for its index lock exactly the way a remote one does, and the
        # errors raised here already declare themselves retryable, so without
        # this the SAME error type meant "retried" or "failed at once"
        # depending only on which backend an operator had configured, with the
        # default being the half that did not retry.
        self._retry = GeneralRetryHandler(
            retryable=_is_retryable_local_git_op,
            max_attempts=cfg.max_attempts,
            base=cfg.base_delay_seconds,
            cap=cfg.cap_delay_seconds,
            event=GIT_BACKEND_PUSH_RETRY,
            jitter=cfg.jitter,
            clock=self._clock,
        )

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
        """Create the bare repo + working tree (idempotent).

        Returns:
            A :class:`ProvisionResult` with ``newly_created=True``
            after fresh creation, or ``newly_created=False`` when
            the working tree was already a git repo.

        Raises:
            GitBackendProvisionError: When workspace dir creation or
                any ``git`` invocation during provisioning fails.
        """
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
            await asyncio.to_thread(ensure_shared_dir, bare)
            await asyncio.to_thread(ensure_shared_dir, workspace_path)
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
        await transfer_ref_local(
            source_root=workspace_path,
            target_git_dir=bare,
            source_ref=str(default_branch),
            target_ref=f"refs/heads/{default_branch}",
            cmd_timeout=self._cmd_timeout,
            failure=GitFailure(
                exc=GitBackendProvisionError,
                project_id=pid,
                event=GIT_BACKEND_PROVISION_FAILED,
            ),
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
        """Import *source* into the working tree, then push to the bare repo.

        Returns:
            A :class:`SeedResult` recording the repo root, default
            branch, head SHA after the seed push, and source kind.
        """
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
        await transfer_ref_local(
            source_root=repo_root,
            target_git_dir=self._bare_repo_path(pid),
            source_ref=str(default_branch),
            target_ref=f"refs/heads/{default_branch}",
            force=True,
            cmd_timeout=self._cmd_timeout,
            failure=GitFailure(
                exc=GitBackendSeedError,
                project_id=pid,
                event=GIT_BACKEND_SEED_FAILED,
            ),
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
        """Push *branch* to the project's bare repo; return its head SHA.

        Returns:
            A :class:`PushResult` carrying the branch name and the
            head SHA observed after the push.
        """
        pid = str(project_id)

        async def _attempt() -> None:
            await transfer_ref_local(
                source_root=repo_root,
                target_git_dir=self._bare_repo_path(pid),
                source_ref=str(branch),
                target_ref=f"refs/heads/{branch}",
                cmd_timeout=self._cmd_timeout,
                failure=GitFailure(
                    exc=GitBackendPushError,
                    project_id=pid,
                    event=GIT_BACKEND_PUSH_FAILED,
                ),
            )

        await self._retry.execute(_attempt, project_id=pid, branch=str(branch))
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
        """Fetch *branch* from the project's bare repo into *repo_root*.

        Returns:
            A :class:`FetchResult` listing the refs updated by the
            fetch (may be empty when nothing changed).

        Raises:
            GitBackendFetchError: When no branch is named. The bundle
                transport this backend uses in place of git's
                shell-dependent local transport carries the refs it was
                asked for, so "everything the remote has" is a question it
                cannot answer. Refusing says so; returning an empty success
                claims a fetch that did not happen, and the sibling
                external-remote backend answers the same call with a real
                full fetch, so silence here would make one protocol mean two
                things.
        """
        pid = str(project_id)
        if branch is None:
            msg = (
                f"embedded fetch for project {pid!r} needs a branch: the "
                "bundle transport cannot enumerate the remote's refs"
            )
            logger.warning(
                GIT_BACKEND_FETCH_FAILED, project_id=pid, reason="no_branch_named"
            )
            raise GitBackendFetchError(msg)

        async def _attempt() -> None:
            await transfer_ref_local(
                source_root=self._bare_repo_path(pid),
                target_git_dir=repo_root / _GIT_DIR,
                source_ref=f"refs/heads/{branch}",
                target_ref=f"refs/remotes/{REMOTE_NAME}/{branch}",
                cmd_timeout=self._cmd_timeout,
                failure=GitFailure(
                    exc=GitBackendFetchError,
                    project_id=pid,
                    event=GIT_BACKEND_FETCH_FAILED,
                ),
            )

        await self._retry.execute(_attempt, project_id=pid, branch=str(branch))
        logger.info(GIT_BACKEND_FETCH_COMPLETE, project_id=pid)
        return FetchResult(updated_refs=(NotBlankStr(str(branch)),))


__all__ = ["EmbeddedGitBackend"]
