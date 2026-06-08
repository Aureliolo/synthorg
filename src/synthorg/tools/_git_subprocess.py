"""Raw git subprocess I/O helpers for ``_BaseGitTool``.

Holds the credential/stderr sanitisers and the four subprocess-execution
helpers (process start, timed wait, output decoding, and sandbox-result
conversion) that ``_git_base`` composes, leaving the base-tool module to
own workspace-boundary validation and command orchestration.

All output that can reach the LLM (``ToolExecutionResult.content``) or the
log stream is scrubbed of embedded credentials and control characters here.
"""

import asyncio
import contextlib
import re
from pathlib import Path
from typing import Final

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.git import (
    GIT_COMMAND_FAILED,
    GIT_COMMAND_SUCCESS,
    GIT_COMMAND_TIMEOUT,
)
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.sandbox.result import SandboxResult

logger = get_logger(__name__)

_DEFAULT_KILL_GRACE_SECONDS: Final[float] = 5.0
"""Fallback kill-grace for git subprocess teardown.

Mirrors the ``tools.git_kill_grace_timeout_seconds`` setting default.
``_await_git_process`` uses this constant directly because this module
has no access to the ``ConfigResolver`` boundary.
"""

# Matches http(s)://userinfo@host patterns in git URLs.
_CREDENTIAL_RE = re.compile(r"(https?://)[^@/]+@")

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]+")
_MAX_STDERR_FRAGMENT: Final[int] = 500


def _sanitize_command(args: list[str]) -> list[str]:
    """Redact embedded credentials from git command args for logging.

    Returns:
        List of ``str``.
    """
    return [_CREDENTIAL_RE.sub(r"\1***@", a) for a in args]


def _sanitize_stderr(raw: str) -> str:
    """Replace control characters, redact credentials, and truncate.

    All control characters (including newlines, tabs, and carriage
    returns) are collapsed into single spaces to prevent log injection
    and LLM prompt injection via stderr content.  Embedded credentials
    (``https://user:token@host``) are redacted before truncation.

    Returns:
        Result of type ``str``.
    """
    sanitized = _CONTROL_CHAR_RE.sub(" ", raw).strip()
    return _CREDENTIAL_RE.sub(r"\1***@", sanitized)[:_MAX_STDERR_FRAGMENT]


async def _start_git_process(
    args: list[str],
    *,
    work_dir: Path,
    env: dict[str, str],
) -> asyncio.subprocess.Process | ToolExecutionResult:
    """Start the git subprocess, returning an error on failure.

    Args:
        args: Git command arguments.
        work_dir: Working directory for the subprocess.
        env: Environment variables for the subprocess.

    Returns:
        The started ``Process`` on success, or a
        ``ToolExecutionResult`` with ``is_error=True`` on failure.
    """
    try:
        return await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=work_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except OSError as exc:
        # Drop exc_info + scrub. Git OSError messages can carry
        # the working-directory path which may include user
        # namespaces / repo URLs.
        logger.warning(
            GIT_COMMAND_FAILED,
            command=_sanitize_command(["git", *args]),
            reason="subprocess_start_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ToolExecutionResult(
            content="Failed to start git process",
            is_error=True,
        )


async def _await_git_process(
    proc: asyncio.subprocess.Process,
    args: list[str],
    *,
    deadline: float,
) -> tuple[bytes, bytes] | ToolExecutionResult:
    """Wait for the process with a timeout, returning output or error.

    On timeout, kills the process and waits up to 5 seconds for
    termination before returning an error result.

    Args:
        proc: The running subprocess.
        args: Git command arguments (for logging).
        deadline: Seconds before the process is killed.

    Returns:
        A ``(stdout, stderr)`` tuple on success, or a
        ``ToolExecutionResult`` with ``is_error=True`` on timeout.
    """
    try:
        return await asyncio.wait_for(
            proc.communicate(),
            timeout=deadline,
        )
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        stderr_fragment = ""
        try:
            _, raw_stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=_DEFAULT_KILL_GRACE_SECONDS,
            )
            raw = raw_stderr.decode("utf-8", errors="replace").strip()
            # Sanitize: strip control chars and truncate for safety.
            stderr_fragment = _sanitize_stderr(raw)
        except TimeoutError:
            logger.warning(
                GIT_COMMAND_FAILED,
                command=_sanitize_command(["git", *args]),
                error="process did not terminate after kill",
            )
        logger.warning(
            GIT_COMMAND_TIMEOUT,
            command=_sanitize_command(["git", *args]),
            deadline=deadline,
            stderr_fragment=stderr_fragment,
        )
        msg = f"Git command timed out after {deadline}s"
        if stderr_fragment:
            msg += f": {stderr_fragment}"
        return ToolExecutionResult(
            content=msg,
            is_error=True,
        )


def _process_git_output(
    args: list[str],
    returncode: int | None,
    stdout_bytes: bytes,
    stderr_bytes: bytes,
) -> ToolExecutionResult:
    """Decode output and build the result.

    Prefers stderr for error content; falls back to stdout, then
    a generic "Unknown git error" message.

    Args:
        args: Git command arguments (for logging).
        returncode: Process exit code (``None`` treated as error).
        stdout_bytes: Raw stdout from the process.
        stderr_bytes: Raw stderr from the process.

    Returns:
        A ``ToolExecutionResult`` with decoded content.
    """
    stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
    if returncode != 0:
        # Git auth-failure stderr commonly echoes the remote URL
        # with embedded userinfo
        # (``https://user:token@host/...``); ``_sanitize_stderr``
        # strips those tokens. Both the log field and the
        # LLM-facing tool result must use the scrubbed copy.
        sanitized_stderr = _sanitize_stderr(stderr)
        sanitized_stdout = _sanitize_stderr(stdout)
        logger.warning(
            GIT_COMMAND_FAILED,
            command=_sanitize_command(["git", *args]),
            returncode=returncode,
            stderr=sanitized_stderr,
            stdout=sanitized_stdout,
        )
        return ToolExecutionResult(
            content=sanitized_stderr or sanitized_stdout or "Unknown git error",
            is_error=True,
        )
    logger.debug(GIT_COMMAND_SUCCESS, command=_sanitize_command(["git", *args]))
    return ToolExecutionResult(content=stdout)


def _sandbox_result_to_execution_result(
    args: list[str],
    result: SandboxResult,
    *,
    deadline: float,
) -> ToolExecutionResult:
    """Convert a ``SandboxResult`` to a ``ToolExecutionResult``.

    Mirrors ``_process_git_output`` but operates on the sandbox
    result model.

    Args:
        args: Git command arguments (for logging).
        result: The sandbox execution result.
        deadline: Timeout that was used (for logging).

    Returns:
        A ``ToolExecutionResult`` with the appropriate content.
    """
    if result.timed_out:
        stderr_fragment = (
            _sanitize_stderr(result.stderr.strip()) if result.stderr else ""
        )
        logger.warning(
            GIT_COMMAND_TIMEOUT,
            command=_sanitize_command(["git", *args]),
            deadline=deadline,
            stderr_fragment=stderr_fragment,
        )
        msg = f"Git command timed out after {deadline}s"
        if stderr_fragment:
            msg += f": {stderr_fragment}"
        return ToolExecutionResult(
            content=msg,
            is_error=True,
        )
    if result.returncode != 0:
        # Same scrub as ``_process_git_output`` -- sandbox
        # stderr/stdout can carry remote-URL userinfo on auth
        # failure paths.
        sanitized_stderr = _sanitize_stderr(result.stderr) if result.stderr else ""
        sanitized_stdout = _sanitize_stderr(result.stdout) if result.stdout else ""
        logger.warning(
            GIT_COMMAND_FAILED,
            command=_sanitize_command(["git", *args]),
            returncode=result.returncode,
            stderr=sanitized_stderr,
            stdout=sanitized_stdout,
        )
        return ToolExecutionResult(
            content=(sanitized_stderr or sanitized_stdout or "Unknown git error"),
            is_error=True,
        )
    logger.debug(GIT_COMMAND_SUCCESS, command=_sanitize_command(["git", *args]))
    return ToolExecutionResult(content=result.stdout)
