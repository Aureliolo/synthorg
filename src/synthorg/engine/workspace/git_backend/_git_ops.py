"""Shared git subprocess helpers for the git-backend strategies.

System-internal (not agent-facing).  Wraps
:func:`~synthorg.engine.workspace._git_subprocess.run_git_subprocess`
with typed-error raising so each strategy stays terse and every
failure surfaces as a :class:`~synthorg.engine.errors.GitBackendError`.
"""

import asyncio
from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649 introspection)
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
