"""Process-lifecycle helpers for the subprocess sandbox.

Stateless functions for spawning, killing, and closing child processes, plus
the credential redactor and the platform default-PATH directories. Split out
of ``subprocess_sandbox`` so the backend module stays focused on the execute
flow and workspace validation.
"""

import asyncio
import contextlib
import os
import re
import signal
from pathlib import Path
from typing import Final

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.sandbox import (
    SANDBOX_KILL_FALLBACK,
    SANDBOX_SPAWN_FAILED,
)
from synthorg.tools._process_cleanup import close_subprocess_transport
from synthorg.tools.sandbox.errors import SandboxStartError

logger = get_logger(__name__)

# Unix process-group support for killing child process trees.
_HAS_PROCESS_GROUPS: Final[bool] = hasattr(os, "killpg")

# Matches http(s)://user:pass@host patterns in URLs.
_CREDENTIAL_RE = re.compile(r"(https?://)[^@/]+@")


def _redact_args(args: tuple[str, ...]) -> tuple[str, ...]:
    """Redact embedded credentials from command args for logging.

    Returns:
        Tuple of ``str``.
    """
    return tuple(_CREDENTIAL_RE.sub(r"\1***@", a) for a in args)


def _get_platform_default_dirs() -> tuple[str, ...]:
    """Return built-in safe PATH directories for the current platform.

    These are built-in system directories -- not influenced by
    ``SubprocessSandboxConfig`` user configuration.  On Windows,
    ``SYSTEMROOT`` is read from the process environment at call
    time (with a safe default fallback).

    Returns:
        Tuple of ``str``.
    """
    if os.name == "nt":
        system_root = os.environ.get("SYSTEMROOT", r"C:\WINDOWS")
        return (
            system_root,
            str(Path(system_root) / "system32"),
            r"C:\Program Files\Git",
            r"C:\Program Files (x86)\Git",
        )
    return ("/usr/bin", "/usr/local/bin", "/bin", "/usr/sbin", "/sbin")


def _kill_process(proc: asyncio.subprocess.Process) -> None:
    """Kill the process, targeting the process group on Unix.

    On Unix with ``start_new_session=True``, kills the entire
    process group to prevent orphaned grandchild processes.
    Falls back to direct ``proc.kill()`` on Windows or on error.
    Handles ``ProcessLookupError`` when the process already exited.
    """
    if _HAS_PROCESS_GROUPS:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)  # type: ignore[attr-defined,unused-ignore]
        except ProcessLookupError:
            return
        except OSError as kill_exc:
            logger.warning(
                SANDBOX_KILL_FALLBACK,
                pid=proc.pid,
                error_type=type(kill_exc).__name__,
                error=safe_error_description(kill_exc),
            )
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            return
        else:
            return
    with contextlib.suppress(ProcessLookupError):
        proc.kill()


def _close_process(proc: asyncio.subprocess.Process) -> None:
    """Close subprocess transport to prevent ResourceWarning on Windows.

    Delegates to :func:`close_subprocess_transport` -- see its
    docstring for details on the CPython-internal ``_transport``
    access and error handling.
    """
    close_subprocess_transport(proc)


async def _spawn_process(
    command: str,
    args: tuple[str, ...],
    work_dir: Path,
    env: dict[str, str],
) -> asyncio.subprocess.Process:
    """Start the subprocess, raising on failure.

    Args:
        command: Executable name or path.
        args: Command arguments.
        work_dir: Working directory.
        env: Filtered environment.

    Returns:
        Result of type ``asyncio.subprocess.Process``.

    Raises:
        SandboxStartError: If the subprocess could not be started.
    """
    try:
        return await asyncio.create_subprocess_exec(
            command,
            *args,
            cwd=work_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=_HAS_PROCESS_GROUPS,
        )
    except OSError as exc:
        logger.warning(
            SANDBOX_SPAWN_FAILED,
            command=command,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to start '{command}': {safe_error_description(exc)}"
        raise SandboxStartError(
            msg,
            context={"command": command},
        ) from exc
