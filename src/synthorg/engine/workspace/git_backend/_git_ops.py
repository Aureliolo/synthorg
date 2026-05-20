"""Shared git subprocess helpers for the git-backend strategies.

System-internal (not agent-facing).  Wraps
:func:`~synthorg.engine.workspace._git_subprocess.run_git_subprocess`
with typed-error raising so each strategy stays terse and every
failure surfaces as a :class:`~synthorg.engine.errors.GitBackendError`.
"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from synthorg.engine.workspace._git_subprocess import (
    _redact_args,
    run_git_subprocess,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    GIT_BACKEND_PROVISION_FAILED,
)

if TYPE_CHECKING:
    from synthorg.engine.errors import GitBackendError

logger = get_logger(__name__)


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
        return
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
