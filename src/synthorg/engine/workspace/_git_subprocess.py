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
from collections.abc import Mapping

# ``Path`` is imported at runtime (not under TYPE_CHECKING) because it is used
# in a runtime-evaluated annotation on ``run_git_subprocess``; under PEP 649
# lazy annotations ``inspect.get_annotations`` resolves these in module globals,
# so a TYPE_CHECKING-only import would raise ``NameError`` at introspection time.
from pathlib import Path
from typing import Final

from synthorg.core.git_env import (
    GIT_HARDENING_OVERRIDES,
    LOCAL_TRANSPORT_GIT_CONFIG,
    NO_HOOKS_GIT_CONFIG,
    RELATIVE_WORKTREE_GIT_CONFIG,
    SHARED_GROUP_GIT_CONFIG,
    git_config_env,
)
from synthorg.core.tls_trust import git_tls_config
from synthorg.core.url_redaction import redact_url
from synthorg.observability import get_logger
from synthorg.observability.redaction import scrub_secret_tokens

logger = get_logger(__name__)

# Three unrelated causes, three codes. A single shared sentinel made "git is
# not installed in this image" indistinguishable from "git rejected the
# command", so the operator-facing error named a return code and nothing else.
# Kept below every POSIX exit status (git's own top out at 128 + signal) so a
# real git code can never collide with one.
GIT_RC_BINARY_NOT_FOUND: Final[int] = -201
GIT_RC_SPAWN_FAILED: Final[int] = -202
GIT_RC_TIMED_OUT: Final[int] = -203
GIT_RC_MISSING_REPO_ROOT: Final[int] = -204

_FAILURE_DESCRIPTIONS: Final[dict[int, str]] = {
    GIT_RC_BINARY_NOT_FOUND: "the 'git' binary is not on PATH",
    GIT_RC_SPAWN_FAILED: "the git subprocess could not be spawned",
    GIT_RC_TIMED_OUT: "the git command timed out",
    GIT_RC_MISSING_REPO_ROOT: (
        "the repository directory it was told to run in does not exist"
    ),
}


def describe_git_failure(return_code: int) -> str | None:
    """Return why git never ran, or ``None`` when it ran and exited itself.

    Args:
        return_code: The code :func:`run_git_subprocess` handed back.

    Returns:
        An operator-facing cause for a code git never produced, or
        ``None`` for a genuine git exit status (including success).
    """
    return _FAILURE_DESCRIPTIONS.get(return_code)


def git_failure_detail(return_code: int) -> str:
    """Render *return_code* for an error message a human will read.

    Args:
        return_code: The code :func:`run_git_subprocess` handed back.

    Returns:
        The cause when git never ran, else the bare return code, which is
        all the code alone can say about a command git itself rejected.
        Pair it with :func:`git_stderr_summary` for git's own account.
    """
    return describe_git_failure(return_code) or f"rc={return_code}"


#: How much of git's stderr to carry into a failure. Git writes its
#: explanation in the closing line or two and can precede it with a progress
#: firehose, so the tail is the part that says what went wrong.
GIT_STDERR_TAIL_CHARS: Final[int] = 400


def git_stderr_summary(stderr: str) -> str | None:
    """Return git's own account of a failure, scrubbed and bounded.

    A return code alone does not identify a git failure: 128 covers a
    missing upstream, a rejected non-fast-forward, a refused hook and an
    unreadable repository alike. Discarding this stream left a live
    ``rc=128`` with nothing to diagnose it by.

    Args:
        stderr: The raw stderr :func:`run_git_subprocess` captured.

    Returns:
        The redacted tail of *stderr*, or ``None`` when git said nothing.
    """
    scrubbed = scrub_secret_tokens(stderr).strip()
    if not scrubbed:
        return None
    return scrubbed[-GIT_STDERR_TAIL_CHARS:]


def _sanitised_env(config: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a hardened environment for one git invocation.

    When this code runs from inside a git pre-push hook (or any caller
    whose own cwd is a git working tree), git inherits ``GIT_DIR`` /
    ``GIT_WORK_TREE`` / ``GIT_COMMON_DIR`` from the parent process and
    those override ordinary path-based repo discovery. A child ``git
    rev-parse --is-inside-work-tree`` then reports the PARENT repo even
    though we passed a fresh tmp-dir as ``cwd``. Every inherited ``GIT_``
    variable is therefore dropped, and the hardening overrides the
    agent-facing tools spawn under are applied on top of the result, so
    the two git paths cannot diverge on how much of the host they trust.

    The hardening also cuts out the host's TLS trust, which is why
    :func:`git_tls_config` travels with each invocation: an operator's
    additional CA (or their deliberate verify-off) is configured in the
    product and reaches both this path and the httpx clients, rather than
    being read from a ``~/.gitconfig`` this environment no longer sees.

    Every repository reached from here is one the system named itself, so
    :data:`LOCAL_TRANSPORT_GIT_CONFIG` travels with each invocation: the
    hardening's ``GIT_PROTOCOL_FROM_USER=0`` otherwise refuses the file
    transport, which is what a bare repo at a local path speaks. It merges
    into a single mapping with *config* rather than rendering separately,
    because both share one ``GIT_CONFIG_COUNT``.

    Args:
        config: Per-invocation git config, for a credential that must
            reach git without being written anywhere it outlives the
            command.

    Returns:
        The child environment.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_HARDENING_OVERRIDES)
    env.update(
        git_config_env(
            {
                **LOCAL_TRANSPORT_GIT_CONFIG,
                **SHARED_GROUP_GIT_CONFIG,
                **RELATIVE_WORKTREE_GIT_CONFIG,
                **NO_HOOKS_GIT_CONFIG,
                **git_tls_config(),
                **(config or {}),
            }
        )
    )
    return env


def _redact_arg(arg: str) -> str:
    """Strip embedded userinfo from a URL-looking arg, leave others as-is.

    The external-remote backend invokes ``git clone https://x-access-token:
    TOKEN@host/...`` style URLs. Without redaction the token would land in the
    structured log when the spawn / timeout / cancellation handlers below
    record the failing args.

    Returns:
        The arg with userinfo stripped when it looks like a URL with
        embedded credentials, and with any other credential pattern
        masked.
    """
    # Fast pass-through for plain args / credential-free URLs avoids any
    # reformatting; only a URL carrying ``user:token@`` is reconstructed.
    if "://" not in arg or "@" not in arg:
        # A credential does not have to arrive as a URL: ``-c
        # http.extraHeader=Authorization: Bearer <token>`` is an ordinary
        # argument that the URL branch below would pass through verbatim.
        return scrub_secret_tokens(arg)
    return scrub_secret_tokens(redact_url(arg, query="keep"))


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
    config: Mapping[str, str] | None = None,
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
        config: Per-invocation git config, passed through the
            environment. This is how a forge credential reaches git
            without landing in the process arguments or in the
            workspace's own ``.git/config``.

    Returns:
        Tuple ``(return_code, stdout_text, stderr_text)``. When git never
        ran the code is one of :data:`GIT_RC_BINARY_NOT_FOUND`,
        :data:`GIT_RC_SPAWN_FAILED` or :data:`GIT_RC_TIMED_OUT`, and
        :func:`describe_git_failure` renders the cause.

    Raises:
        asyncio.CancelledError: Propagated (the native path kills the
            child first; the thread fallback lets the short-lived git
            command finish on its worker thread).
    """
    # ``create_subprocess_exec`` can raise ``OSError`` before the process
    # ever starts (missing ``git`` binary, bad ``cwd``, resource limits,
    # ...). Returning the normal contract as ``(<code>, "", <message>)``
    # keeps every caller simple -- they already handle the non-zero rc
    # branch and do not have to special-case a thrown exception.
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(repo_root),
            env=_sanitised_env(config),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except NotImplementedError:
        return await _run_git_in_thread(
            repo_root,
            args,
            cmd_timeout=cmd_timeout,
            log_event=log_event,
            config=config,
        )
    except OSError as exc:
        return _spawn_failure(exc, args, repo_root=repo_root, log_event=log_event)
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
        return (GIT_RC_TIMED_OUT, "", msg)
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

    rc = proc.returncode if proc.returncode is not None else GIT_RC_SPAWN_FAILED
    return (
        rc,
        stdout_bytes.decode("utf-8", errors="replace").strip(),
        stderr_bytes.decode("utf-8", errors="replace").strip(),
    )


def _spawn_failure(
    exc: OSError,
    args: tuple[str, ...],
    *,
    repo_root: Path,
    log_event: str,
) -> tuple[int, str, str]:
    """Classify an ``OSError`` raised before git ever started.

    "The image does not ship git" and "the directory we were told to run in
    is not there" are different operator actions, and the exception type
    alone does not separate them: a missing *cwd* raises
    ``FileNotFoundError`` on POSIX exactly as a missing executable does, and
    ``NotADirectoryError`` on Windows. Reporting the first as the second
    sends an operator hunting for a binary that is installed, which is the
    misdiagnosis this classification exists to remove, so the directory is
    checked before the type is trusted.

    Returns:
        The ``(return_code, "", message)`` triple for the failure.
    """
    if not repo_root.is_dir():
        rc = GIT_RC_MISSING_REPO_ROOT
    elif isinstance(exc, FileNotFoundError):
        rc = GIT_RC_BINARY_NOT_FOUND
    else:
        rc = GIT_RC_SPAWN_FAILED
    msg = f"failed to run git: {_FAILURE_DESCRIPTIONS[rc]}"
    logger.warning(
        log_event,
        error_type=exc.__class__.__name__,
        error=msg,
        repo_root=str(repo_root),
        args=_redact_args(args),
    )
    return (rc, "", msg)


async def _run_git_in_thread(
    repo_root: Path,
    args: tuple[str, ...],
    *,
    cmd_timeout: float,
    log_event: str,
    config: Mapping[str, str] | None = None,
) -> tuple[int, str, str]:
    """Loop-agnostic git fallback: blocking ``subprocess.run`` on a thread.

    Used only when the running event loop cannot spawn subprocesses (the
    Windows ``SelectorEventLoop``). Honours the same
    ``(return_code, stdout, stderr)`` contract as the native path,
    including its failure codes.

    Returns:
        Tuple ``(return_code, stdout_text, stderr_text)``, carrying one of
        the module's failure codes when git never ran.
    """

    def _run() -> tuple[int, str, str]:
        # A list argv with no ``shell=True`` is injection-safe: git and its
        # args go straight to ``CreateProcessW`` with no shell interpreting
        # metacharacters. ``check=False`` because callers handle the
        # non-zero rc branch themselves.
        completed = subprocess.run(  # noqa: S603 -- list argv, no shell
            ["git", *args],  # noqa: S607 -- git resolved from PATH, as everywhere
            cwd=str(repo_root),
            env=_sanitised_env(config),
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
        return (GIT_RC_TIMED_OUT, "", msg)
    except OSError as exc:
        return _spawn_failure(exc, args, repo_root=repo_root, log_event=log_event)
