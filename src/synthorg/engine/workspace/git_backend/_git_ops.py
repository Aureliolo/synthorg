"""Shared git subprocess helpers for the git-backend strategies.

System-internal (not agent-facing).  Wraps
:func:`~synthorg.engine.workspace._git_subprocess.run_git_subprocess`
with typed-error raising so each strategy stays terse and every
failure surfaces as a :class:`~synthorg.engine.errors.GitBackendError`.
"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Final

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import GitBackendSeedError
from synthorg.engine.workspace._git_subprocess import (
    _redact_args,
    run_git_subprocess,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    GIT_BACKEND_PROVISION_FAILED,
    GIT_BACKEND_SEED_FAILED,
)

if TYPE_CHECKING:
    from synthorg.engine.errors import GitBackendError
    from synthorg.engine.workspace.git_backend.protocol import ResolvedSource

logger = get_logger(__name__)

BOT_NAME: Final[str] = "SynthOrg"
BOT_EMAIL: Final[str] = "synthorg-bot@synthorg.local"
REMOTE_NAME: Final[str] = "origin"


async def git(
    repo_root: Path,
    *args: str,
    cmd_timeout: float,
    fail_exc: type[GitBackendError],
    project_id: str,
    event: str = GIT_BACKEND_PROVISION_FAILED,
) -> str:
    """Run ``git *args`` in *repo_root*; raise *fail_exc* on failure.

    Args:
        repo_root: Working directory for the git command.
        *args: Git command arguments.
        cmd_timeout: Maximum seconds the subprocess may run.
        fail_exc: Exception type raised on non-zero exit / spawn failure.
        project_id: Project identifier for structured logging.
        event: Structured-log event constant for failures; defaults to
            ``GIT_BACKEND_PROVISION_FAILED`` so existing call sites stay
            quiet, but push/fetch sites should pass their own event so
            the log discriminates between operations.

    Returns:
        Decoded stdout (stripped) on success.

    Raises:
        GitBackendError: *fail_exc* instance when the command exits
            non-zero or the process could not be spawned.
    """
    rc, stdout, _stderr = await run_git_subprocess(
        repo_root,
        *args,
        cmd_timeout=cmd_timeout,
        log_event=event,
    )
    if rc != 0:
        logger.warning(
            event,
            project_id=project_id,
            git_args=_redact_args(args[:2]),
            return_code=rc,
        )
        msg = f"git {args[0] if args else ''} failed (rc={rc})"
        raise fail_exc(msg)
    return stdout.strip()


async def is_git_repo(path: Path, *, cmd_timeout: float) -> bool:
    """Return ``True`` if *path* is inside a git working tree."""
    if not await asyncio.to_thread(path.exists):
        return False
    rc, stdout, _stderr = await run_git_subprocess(
        path,
        "rev-parse",
        "--is-inside-work-tree",
        cmd_timeout=cmd_timeout,
        log_event=GIT_BACKEND_PROVISION_FAILED,
    )
    return rc == 0 and stdout.strip() == "true"


async def assert_standalone_repo(
    repo_root: Path,
    *,
    cmd_timeout: float,
    fail_exc: type[GitBackendError],
    project_id: str,
) -> None:
    """Refuse to proceed when *repo_root* is a worktree or shares config.

    The bot identity used by the git backends is set via ``git config
    user.email`` / ``user.name``. In a standalone repo that writes to
    ``<repo_root>/.git/config`` and stays scoped to the agent workspace.
    But if *repo_root* is a linked worktree (``.git`` is a file pointing
    at ``<common_dir>/worktrees/<name>``) or a path that resolves to a
    shared common_dir, the very same ``git config`` invocation mutates
    the MAIN repository's shared config -- silently rewriting the
    operator's ``user.{name,email}`` for every other worktree of that
    repo. We've been bitten by that exact scenario; this guard catches
    it before the writes land.

    ``git rev-parse --git-common-dir`` returns the shared common dir
    for a worktree and the repo's own ``.git`` dir for a standalone
    repo. Comparing it against ``<repo_root>/.git`` is the cheapest
    standalone-vs-worktree discriminator git exposes. Crucially, that
    flag returns a RELATIVE path on a standalone repo (just ``.git``);
    resolving such a relative result inside the Python process would
    bind it to the Python cwd (often the synthorg repo itself), which
    is exactly the false-positive trap that originally hid the leak.
    Resolve the common-dir against ``repo_root`` instead so the
    comparison reflects git's view, not Python's.
    """
    rc, common_dir, _stderr = await run_git_subprocess(
        repo_root,
        "rev-parse",
        "--git-common-dir",
        cmd_timeout=cmd_timeout,
        log_event=GIT_BACKEND_PROVISION_FAILED,
    )
    if rc != 0:
        # Fail closed. The whole point of this probe is to prevent
        # identity writes from leaking into a shared parent config; if
        # the probe itself fails we cannot prove the workspace is
        # standalone, so the safe move is to abort provisioning rather
        # than let the unguarded `git config user.{name,email}` writes
        # proceed.
        logger.warning(
            GIT_BACKEND_PROVISION_FAILED,
            project_id=project_id,
            reason="git_common_dir_probe_failed",
            repo_root=str(repo_root),
            return_code=rc,
        )
        msg = (
            f"failed to resolve git-common-dir for {repo_root!s} "
            f"(rc={rc}); refusing identity configuration"
        )
        raise fail_exc(msg)
    common_path = Path(common_dir.strip())
    if not common_path.is_absolute():
        common_path = repo_root / common_path
    common = await asyncio.to_thread(common_path.resolve)
    expected = await asyncio.to_thread((repo_root / ".git").resolve)
    if common == expected:
        return
    logger.warning(
        GIT_BACKEND_PROVISION_FAILED,
        project_id=project_id,
        reason="shared_common_dir",
        repo_root=str(repo_root),
        common_dir=str(common),
    )
    msg = (
        f"refusing to configure git identity on {repo_root!s}: its "
        f"git-common-dir is {common!s}, which is shared with another "
        "repo or worktree. user.email / user.name writes here would "
        "leak to that shared config and rewrite the operator's identity "
        "across every linked worktree."
    )
    raise fail_exc(msg)


async def reject_if_nested_in_parent_worktree(
    path: Path,
    *,
    cmd_timeout: float,
    fail_exc: type[GitBackendError],
    project_id: str,
) -> None:
    """Refuse if *path* sits inside an EXISTING parent working tree.

    ``rev-parse --show-toplevel`` succeeds and reports a toplevel
    DIFFERENT from *path* iff some parent dir of *path* is a working
    tree; in that case ``git init`` would silently no-op and the
    subsequent ``git config`` / ``git commit`` would mutate the outer
    repo. Raise instead of silently corrupting it. If git already
    considers *path* itself the toplevel (idempotent re-provision), the
    caller may proceed.
    """
    rc, stdout, _stderr = await run_git_subprocess(
        path,
        "rev-parse",
        "--show-toplevel",
        cmd_timeout=cmd_timeout,
        log_event=GIT_BACKEND_PROVISION_FAILED,
    )
    if rc != 0:
        return
    toplevel = await asyncio.to_thread(Path(stdout.strip()).resolve)
    if toplevel == await asyncio.to_thread(path.resolve):
        return
    logger.warning(
        GIT_BACKEND_PROVISION_FAILED,
        project_id=project_id,
        reason="nested_in_parent_worktree",
        path=str(path),
        parent_toplevel=str(toplevel),
    )
    msg = (
        f"refusing to provision project {project_id!r} inside an existing "
        f"parent git working tree at {toplevel!s}"
    )
    raise fail_exc(msg)


async def configure_identity(
    workspace_path: Path,
    *,
    cmd_timeout: float,
    fail_exc: type[GitBackendError],
    project_id: str,
) -> None:
    """Set the bot ``user.{email,name}`` after a standalone-repo check."""
    await assert_standalone_repo(
        workspace_path,
        cmd_timeout=cmd_timeout,
        fail_exc=fail_exc,
        project_id=project_id,
    )
    await git(
        workspace_path,
        "config",
        "user.email",
        BOT_EMAIL,
        cmd_timeout=cmd_timeout,
        fail_exc=fail_exc,
        project_id=project_id,
    )
    await git(
        workspace_path,
        "config",
        "user.name",
        BOT_NAME,
        cmd_timeout=cmd_timeout,
        fail_exc=fail_exc,
        project_id=project_id,
    )


async def _assert_workspace_empty(
    repo_root: Path,
    *,
    cmd_timeout: float,
    project_id: str,
) -> None:
    """Raise if *repo_root* already holds a codebase.

    ``ls-files`` alone misses untracked content, and ``status
    --porcelain`` alone misses a clean committed tree (the re-seed
    clobber case). Check both: tracked files OR non-ignored untracked
    files mean the workspace already holds a codebase and seeding would
    clobber it.
    """
    tracked = await git(
        repo_root,
        "ls-files",
        cmd_timeout=cmd_timeout,
        fail_exc=GitBackendSeedError,
        project_id=project_id,
        event=GIT_BACKEND_SEED_FAILED,
    )
    untracked = await git(
        repo_root,
        "ls-files",
        "--others",
        "--exclude-standard",
        cmd_timeout=cmd_timeout,
        fail_exc=GitBackendSeedError,
        project_id=project_id,
        event=GIT_BACKEND_SEED_FAILED,
    )
    if not (tracked.strip() or untracked.strip()):
        return
    logger.warning(
        GIT_BACKEND_SEED_FAILED,
        project_id=project_id,
        reason="workspace_not_empty",
    )
    msg = (
        f"refusing to seed project {project_id!r}: workspace already "
        "contains files (a codebase is already present)"
    )
    raise GitBackendSeedError(msg)


async def import_source_into_worktree(
    repo_root: Path,
    *,
    source: ResolvedSource,
    cmd_timeout: float,
    project_id: str,
) -> NotBlankStr:
    """Fetch *source* into *repo_root* and reset the branch onto its head.

    Requires an empty working tree (no tracked or untracked files):
    provisioning lands a single empty commit, so any content means the
    workspace already holds a codebase and seeding would clobber it. The
    source is fetched directly from its URL (no named remote is
    configured), so a credential embedded in ``fetch_url`` never
    persists in the workspace's git config. Returns the imported head
    SHA.

    Raises:
        GitBackendSeedError: The workspace is non-empty, or the
            fetch / reset failed.
    """
    await _assert_workspace_empty(
        repo_root, cmd_timeout=cmd_timeout, project_id=project_id
    )
    await git(
        repo_root,
        *source.pre_fetch_config_args,
        "fetch",
        str(source.fetch_url),
        "HEAD",
        cmd_timeout=cmd_timeout,
        fail_exc=GitBackendSeedError,
        project_id=project_id,
        event=GIT_BACKEND_SEED_FAILED,
    )
    await git(
        repo_root,
        "reset",
        "--hard",
        "FETCH_HEAD",
        cmd_timeout=cmd_timeout,
        fail_exc=GitBackendSeedError,
        project_id=project_id,
        event=GIT_BACKEND_SEED_FAILED,
    )
    head = await git(
        repo_root,
        "rev-parse",
        "HEAD",
        cmd_timeout=cmd_timeout,
        fail_exc=GitBackendSeedError,
        project_id=project_id,
        event=GIT_BACKEND_SEED_FAILED,
    )
    return NotBlankStr(head)


async def init_working_tree_with_remote(  # noqa: PLR0913 -- irreducible git-init params
    workspace_path: Path,
    *,
    default_branch: str,
    remote_url: str,
    cmd_timeout: float,
    fail_exc: type[GitBackendError],
    project_id: str,
) -> None:
    """Initialise a standalone working tree wired to ``origin``.

    Creates the working tree (``git init`` on *default_branch*), sets
    the bot identity, lands an empty initial commit so worktrees can
    branch from *default_branch*, and adds ``origin`` pointing at
    *remote_url*. Does NOT push: callers that target a not-yet-created
    remote push (and lazily provision the remote) separately.

    The standalone-repo + parent-worktree guards run first so identity
    writes cannot leak into a shared parent config.
    """
    await reject_if_nested_in_parent_worktree(
        workspace_path,
        cmd_timeout=cmd_timeout,
        fail_exc=fail_exc,
        project_id=project_id,
    )
    await git(
        workspace_path,
        "init",
        "--initial-branch",
        default_branch,
        cmd_timeout=cmd_timeout,
        fail_exc=fail_exc,
        project_id=project_id,
    )
    await configure_identity(
        workspace_path,
        cmd_timeout=cmd_timeout,
        fail_exc=fail_exc,
        project_id=project_id,
    )
    await git(
        workspace_path,
        "commit",
        "--allow-empty",
        "-m",
        "Initialise project workspace",
        cmd_timeout=cmd_timeout,
        fail_exc=fail_exc,
        project_id=project_id,
    )
    await git(
        workspace_path,
        "remote",
        "add",
        REMOTE_NAME,
        remote_url,
        cmd_timeout=cmd_timeout,
        fail_exc=fail_exc,
        project_id=project_id,
    )
