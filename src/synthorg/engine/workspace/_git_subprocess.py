"""System-internal git subprocess orchestration.

This module is NOT agent-facing. Agent-facing git operations live in
``synthorg.tools.git_tools`` and carry distinct security validation
(workspace-path confinement, env hardening). This module is a pure
process-management helper for :class:`SynthOrgGitWorktree` setup,
merge, and teardown flows, isolating the subprocess lifecycle from the
high-level worktree coordination logic.
"""

import asyncio
import os
import subprocess

# ``Path`` is imported at runtime (not under TYPE_CHECKING) because it is used
# in a runtime-evaluated annotation on ``run_git_subprocess``; under PEP 649
# lazy annotations ``inspect.get_annotations`` resolves these in module globals,
# so a TYPE_CHECKING-only import would raise ``NameError`` at introspection time.
from pathlib import Path

from synthorg.core.url_redaction import redact_url
from synthorg.observability import get_logger

logger = get_logger(__name__)


def _sanitised_env() -> dict[str, str]:
    """Return ``os.environ`` minus git's discovery-override vars.

    When this code runs from inside a git pre-push hook (or any caller
    whose own cwd is a git working tree), git inherits ``GIT_DIR`` /
    ``GIT_WORK_TREE`` / ``GIT_COMMON_DIR`` from the parent process and
    those override ordinary path-based repo discovery. A child ``git
    rev-parse --is-inside-work-tree`` then reports the PARENT repo even
    though we passed a fresh tmp-dir as ``cwd``. Strip them so our
    git subprocesses see only the path hierarchy under ``cwd``.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _redact_arg(arg: str) -> str:
    """Strip embedded userinfo from a URL-looking arg, leave others as-is.

    The external-remote backend invokes ``git clone https://x-access-token:
    TOKEN@host/...`` style URLs. Without redaction the token would land in the
    structured log when the spawn / timeout / cancellation handlers below
    record the failing args.

    Returns:
        The arg with userinfo stripped when it looks like a URL with
        embedded credentials; the arg unchanged otherwise.
    """
    # Fast pass-through for plain args / credential-free URLs avoids any
    # reformatting; only a URL carrying ``user:token@`` is reconstructed.
    if "://" not in arg or "@" not in arg:
        return arg
    return redact_url(arg, query="keep")


def _redact_args(args: tuple[str, ...]) -> tuple[str, ...]:
    """Redact every URL-looking element of *args* (token-in-URL safe).

    Returns:
        Tuple of args with embedded URL userinfo stripped from each
        element.
    """
    return tuple(_redact_arg(a) for a in args)


async def run_git_subprocess(
    repo_root: Path,
    *args: str,
    cmd_timeout: float,
    log_event: str,
) -> tuple[int, str, str]:
    """Run ``git *args`` in *repo_root* and decode stdout/stderr.

    Prefers the loop-native :func:`asyncio.create_subprocess_exec` (truly
    non-blocking, kills the child on timeout / cancellation). That call
    raises ``NotImplementedError`` on the Windows ``SelectorEventLoop``,
    which has no IOCP subprocess integration and which psycopg's async
    pool forces on Windows; in that one case it transparently falls back
    to a blocking :func:`subprocess.run` on a worker thread so git-backed
    provisioning still runs under every event-loop policy and platform.

    Args:
        repo_root: Working directory for the git command.
        *args: Git command arguments (e.g. ``"worktree"``, ``"add"``).
        cmd_timeout: Maximum seconds to wait for completion.
        log_event: Structured-log event constant used on timeout.

    Returns:
        Tuple ``(return_code, stdout_text, stderr_text)``. On spawn
        failure or timeout, returns ``(-1, "", <message>)``.

    Raises:
        asyncio.CancelledError: Propagated (the native path kills the
            child first; the thread fallback lets the short-lived git
            command finish on its worker thread).
    """
    # ``create_subprocess_exec`` can raise ``OSError`` before the process
    # ever starts (missing ``git`` binary, bad ``cwd``, resource limits,
    # ...). Returning the normal contract as ``(-1, "", <message>)``
    # keeps every caller simple -- they already handle the non-zero rc
    # branch and do not have to special-case a thrown exception.
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(repo_root),
            env=_sanitised_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except NotImplementedError:
        return await _run_git_in_thread(
            repo_root, args, cmd_timeout=cmd_timeout, log_event=log_event
        )
    except OSError as exc:
        msg = f"failed to spawn git subprocess: {exc.__class__.__name__}"
        logger.warning(
            log_event,
            error_type=exc.__class__.__name__,
            error=msg,
            args=_redact_args(args),
        )
        return (-1, "", msg)
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=cmd_timeout,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        msg = f"git {args[0] if args else ''} timed out after {cmd_timeout}s"
        logger.warning(
            log_event,
            error_type="TimeoutError",
            error=msg,
            args=_redact_args(args),
        )
        return (-1, "", msg)
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        logger.warning(
            log_event,
            error_type="CancelledError",
            error="git subprocess cancelled by caller",
            args=_redact_args(args),
        )
        raise

    rc = proc.returncode if proc.returncode is not None else -1
    return (
        rc,
        stdout_bytes.decode("utf-8", errors="replace").strip(),
        stderr_bytes.decode("utf-8", errors="replace").strip(),
    )


async def _run_git_in_thread(
    repo_root: Path,
    args: tuple[str, ...],
    *,
    cmd_timeout: float,
    log_event: str,
) -> tuple[int, str, str]:
    """Loop-agnostic git fallback: blocking ``subprocess.run`` on a thread.

    Used only when the running event loop cannot spawn subprocesses (the
    Windows ``SelectorEventLoop``). Honours the same
    ``(return_code, stdout, stderr)`` / ``(-1, "", <message>)`` contract as
    the native path.

    Returns:
        Tuple ``(return_code, stdout_text, stderr_text)``, or
        ``(-1, "", <message>)`` on spawn failure or timeout.
    """

    def _run() -> tuple[int, str, str]:
        # A list argv with no ``shell=True`` is injection-safe: git and its
        # args go straight to ``CreateProcessW`` with no shell interpreting
        # metacharacters. ``check=False`` because callers handle the
        # non-zero rc branch themselves.
        completed = subprocess.run(  # noqa: S603 -- list argv, no shell
            ["git", *args],  # noqa: S607 -- git resolved from PATH, as everywhere
            cwd=str(repo_root),
            env=_sanitised_env(),
            capture_output=True,
            timeout=cmd_timeout,
            check=False,
        )
        return (
            completed.returncode,
            completed.stdout.decode("utf-8", errors="replace").strip(),
            completed.stderr.decode("utf-8", errors="replace").strip(),
        )

    try:
        return await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired:
        msg = f"git {args[0] if args else ''} timed out after {cmd_timeout}s"
        logger.warning(
            log_event,
            error_type="TimeoutExpired",
            error=msg,
            args=_redact_args(args),
        )
        return (-1, "", msg)
    except OSError as exc:
        msg = f"failed to spawn git subprocess: {exc.__class__.__name__}"
        logger.warning(
            log_event,
            error_type=exc.__class__.__name__,
            error=msg,
            args=_redact_args(args),
        )
        return (-1, "", msg)
