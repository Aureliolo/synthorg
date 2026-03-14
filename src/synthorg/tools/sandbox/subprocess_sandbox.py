"""Subprocess-based sandbox backend.

Executes commands via ``asyncio.create_subprocess_exec`` with
environment filtering, workspace boundary enforcement, timeout
management, and PATH restriction.
"""

import asyncio
import contextlib
import fnmatch
import os
import re
import signal
from pathlib import Path
from typing import TYPE_CHECKING, Final

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.sandbox import (
    SANDBOX_CLEANUP,
    SANDBOX_ENV_FILTERED,
    SANDBOX_EXECUTE_FAILED,
    SANDBOX_EXECUTE_START,
    SANDBOX_EXECUTE_SUCCESS,
    SANDBOX_EXECUTE_TIMEOUT,
    SANDBOX_HEALTH_CHECK,
    SANDBOX_KILL_FAILED,
    SANDBOX_KILL_FALLBACK,
    SANDBOX_PATH_FALLBACK,
    SANDBOX_SPAWN_FAILED,
    SANDBOX_WORKSPACE_VIOLATION,
)
from synthorg.tools._process_cleanup import close_subprocess_transport
from synthorg.tools.sandbox.config import SubprocessSandboxConfig
from synthorg.tools.sandbox.errors import (
    SandboxError,
    SandboxStartError,
)
from synthorg.tools.sandbox.result import SandboxResult

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = get_logger(__name__)

_PATH_SEP = ";" if os.name == "nt" else ":"

_DEFAULT_CONFIG = SubprocessSandboxConfig()

# Unix process-group support for killing child process trees.
_HAS_PROCESS_GROUPS: Final[bool] = hasattr(os, "killpg")

# Matches http(s)://user:pass@host patterns in URLs.
_CREDENTIAL_RE = re.compile(r"(https?://)[^@/]+@")


def _redact_args(args: tuple[str, ...]) -> tuple[str, ...]:
    """Redact embedded credentials from command args for logging."""
    return tuple(_CREDENTIAL_RE.sub(r"\1***@", a) for a in args)


class SubprocessSandbox:
    """Subprocess sandbox backend.

    Runs commands in child processes with filtered environment variables,
    workspace boundary checks, and configurable timeouts.

    Attributes:
        config: Sandbox configuration.
        workspace: Absolute path to the workspace root directory.
    """

    def __init__(
        self,
        *,
        config: SubprocessSandboxConfig | None = None,
        workspace: Path,
    ) -> None:
        """Initialize the subprocess sandbox.

        Args:
            config: Sandbox configuration (defaults to standard config).
            workspace: Absolute path to the workspace root. Must exist.

        Raises:
            ValueError: If *workspace* is not absolute or does not exist.
        """
        if not workspace.is_absolute():
            logger.warning(
                SANDBOX_WORKSPACE_VIOLATION,
                workspace=str(workspace),
                error="workspace must be an absolute path",
            )
            msg = f"workspace must be an absolute path, got: {workspace}"
            raise ValueError(msg)
        resolved = workspace.resolve()
        if not resolved.is_dir():
            logger.warning(
                SANDBOX_WORKSPACE_VIOLATION,
                workspace=str(resolved),
                error="workspace directory does not exist",
            )
            msg = f"workspace directory does not exist: {resolved}"
            raise ValueError(msg)
        self._config = config or _DEFAULT_CONFIG
        self._workspace = resolved

    @property
    def config(self) -> SubprocessSandboxConfig:
        """Sandbox configuration."""
        return self._config

    @property
    def workspace(self) -> Path:
        """Workspace root directory."""
        return self._workspace

    def _matches_allowlist(self, name: str) -> bool:
        """Check if an env var name matches any entry in the allowlist.

        Uses case-insensitive matching on Windows where env var names
        are case-insensitive.
        """
        check_name = name.upper() if os.name == "nt" else name
        for pattern in self._config.env_allowlist:
            check_pattern = pattern.upper() if os.name == "nt" else pattern
            if fnmatch.fnmatch(check_name, check_pattern):
                return True
        return False

    def _matches_denylist(self, name: str) -> bool:
        """Check if an env var name matches any denylist pattern.

        Both name and patterns are uppercased for case-insensitive
        matching — denylist patterns must catch secrets regardless of
        casing.
        """
        upper = name.upper()
        return any(
            fnmatch.fnmatch(upper, pat.upper())
            for pat in self._config.env_denylist_patterns
        )

    def _filter_path(self, path_value: str) -> str:
        """Filter PATH entries, keeping only safe system directories.

        Uses directory-boundary checking to prevent prefix spoofing
        (e.g. ``/usr/bin-malicious`` is rejected even though it starts
        with ``/usr/bin``).  Entries are normalized before comparison.

        When no entries survive filtering, falls back to known safe
        directories that actually exist on the system.
        """
        safe_prefixes = self._get_safe_path_prefixes()
        entries = path_value.split(_PATH_SEP)
        filtered = [e for e in entries if self._is_safe_path_entry(e, safe_prefixes)]
        if filtered:
            return _PATH_SEP.join(filtered)
        logger.warning(
            SANDBOX_PATH_FALLBACK,
            reason="no PATH entries matched safe prefixes; using safe defaults",
            original_entry_count=len(entries),
        )
        # Fallback uses fully hardcoded directories — no os.environ reads,
        # no user-provided extra_safe_path_prefixes — so that the
        # Path.is_dir() filesystem probe receives only compile-time
        # constants (CodeQL py/path-injection).
        fallback_dirs = self._get_hardcoded_fallback_dirs()
        safe_dirs = [p for p in fallback_dirs if Path(p).is_dir()]
        if not safe_dirs:
            logger.error(
                SANDBOX_PATH_FALLBACK,
                reason="no safe PATH directories exist on system",
            )
            msg = (
                "No safe PATH directories found on system; "
                "cannot create safe sandbox environment"
            )
            raise SandboxError(msg)
        return _PATH_SEP.join(safe_dirs)

    @staticmethod
    def _is_safe_path_entry(
        entry: str,
        safe_prefixes: tuple[str, ...],
    ) -> bool:
        """Check if a PATH entry falls within a safe prefix directory.

        Rejects null-byte entries, then uses directory-boundary
        matching to prevent prefix spoofing (e.g. ``/usr/bin-malicious``
        does not match ``/usr/bin``).
        """
        if "\x00" in entry:
            return False
        entry_norm = os.path.normcase(os.path.normpath(entry))
        for prefix in safe_prefixes:
            prefix_norm = os.path.normcase(os.path.normpath(prefix))
            if entry_norm == prefix_norm or entry_norm.startswith(
                prefix_norm + os.sep,
            ):
                return True
        return False

    @staticmethod
    def _get_platform_default_dirs() -> tuple[str, ...]:
        """Return built-in safe PATH directories for the current platform.

        These are built-in system directories — not influenced by
        ``SubprocessSandboxConfig`` user configuration.  On Windows,
        ``SYSTEMROOT`` is read from the process environment at call
        time (with a safe default fallback).
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

    @staticmethod
    def _get_hardcoded_fallback_dirs() -> tuple[str, ...]:
        """Return fully hardcoded safe PATH directories for fallback.

        Unlike ``_get_platform_default_dirs``, this reads **no**
        environment variables — every value is a compile-time constant.
        Used only in the fallback branch of ``_filter_path`` where
        ``Path.is_dir()`` probes the filesystem, so that no
        ``os.environ`` data reaches a filesystem call
        (CodeQL ``py/path-injection``).
        """
        if os.name == "nt":
            return (
                r"C:\WINDOWS",
                r"C:\WINDOWS\system32",
                r"C:\Program Files\Git",
                r"C:\Program Files (x86)\Git",
            )
        return ("/usr/bin", "/usr/local/bin", "/bin", "/usr/sbin", "/sbin")

    def _get_safe_path_prefixes(self) -> tuple[str, ...]:
        """Return safe PATH prefixes for the current platform.

        Combines built-in platform defaults with any extra prefixes
        from ``SubprocessSandboxConfig.extra_safe_path_prefixes``.
        """
        return self._get_platform_default_dirs() + self._config.extra_safe_path_prefixes

    def _build_filtered_env(
        self,
        env_overrides: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """Build a filtered environment for the subprocess.

        Starts with an empty dict, copies allowed vars from the current
        process environment, strips denylist matches, optionally filters
        PATH, then applies overrides.

        Note: ``env_overrides`` bypass the denylist by design — they
        are trusted internal overrides (e.g. git hardening vars).
        Callers must not pass untrusted user-controlled data as
        overrides.

        Args:
            env_overrides: Trusted internal vars applied on top.

        Returns:
            The filtered environment mapping.
        """
        env: dict[str, str] = {}
        filtered_count = 0

        for name, value in os.environ.items():
            allowed = self._matches_allowlist(name)
            denied = self._matches_denylist(name)
            if allowed and not denied:
                env[name] = value
            else:
                filtered_count += 1

        # Case-insensitive key check on Windows where env var names
        # are case-insensitive (e.g. "Path" vs "PATH").
        if self._config.restricted_path and any(k.upper() == "PATH" for k in env):
            path_keys = [k for k in env if k.upper() == "PATH"]
            path_val = next(
                (env[k] for k in reversed(path_keys)),
                "",
            )
            for k in path_keys:
                del env[k]
            env["PATH"] = self._filter_path(path_val)

        if env_overrides:
            env.update(env_overrides)
            # Re-filter PATH if overrides injected one — prevents
            # bypassing the restricted-path guard via env_overrides.
            # Case-insensitive key check on Windows where env var
            # names are case-insensitive (e.g. "Path" vs "PATH").
            if self._config.restricted_path and any(
                k.upper() == "PATH" for k in env_overrides
            ):
                # Consolidate to a canonical PATH key.
                path_keys = [k for k in env if k.upper() == "PATH"]
                path_val = next(
                    (env[k] for k in reversed(path_keys)),
                    "",
                )
                for k in path_keys:
                    del env[k]
                env["PATH"] = self._filter_path(path_val)

        logger.debug(
            SANDBOX_ENV_FILTERED,
            filtered_count=filtered_count,
            kept_count=len(env),
        )
        return env

    def _validate_cwd(self, cwd: Path) -> None:
        """Validate that *cwd* is within the workspace boundary.

        Args:
            cwd: Working directory to validate.

        Raises:
            SandboxError: If *cwd* is outside the workspace and
                ``workspace_only`` is enabled.
        """
        if not self._config.workspace_only:
            return
        try:
            cwd.resolve().relative_to(self._workspace)
        except ValueError as exc:
            logger.warning(
                SANDBOX_WORKSPACE_VIOLATION,
                cwd=str(cwd),
                workspace=str(self._workspace),
            )
            msg = f"Working directory '{cwd}' is outside workspace '{self._workspace}'"
            raise SandboxError(msg) from exc

    @staticmethod
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
                    error=str(kill_exc),
                )
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                return
            else:
                return
        with contextlib.suppress(ProcessLookupError):
            proc.kill()

    @staticmethod
    def _close_process(proc: asyncio.subprocess.Process) -> None:
        """Close subprocess transport to prevent ResourceWarning on Windows.

        Delegates to :func:`close_subprocess_transport` — see its
        docstring for details on the CPython-internal ``_transport``
        access and error handling.
        """
        close_subprocess_transport(proc)

    async def _spawn_process(
        self,
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
                error=str(exc),
                exc_info=True,
            )
            msg = f"Failed to start '{command}': {exc}"
            raise SandboxStartError(
                msg,
                context={"command": command},
            ) from exc

    async def _communicate_with_timeout(
        self,
        proc: asyncio.subprocess.Process,
        command: str,
        args: tuple[str, ...],
        deadline: float,
    ) -> tuple[bytes, bytes, bool]:
        """Wait for process output with timeout handling.

        On timeout, kills the process and captures any partial output.

        Args:
            proc: The running subprocess.
            command: Command name (for logging).
            args: Command arguments (for logging).
            deadline: Seconds before kill.

        Returns:
            Tuple of (stdout_bytes, stderr_bytes, timed_out).
        """
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=deadline,
            )
        except TimeoutError:
            self._kill_process(proc)
            stdout_bytes, stderr_bytes = await self._drain_after_kill(
                proc,
                command,
                args,
            )
            logger.warning(
                SANDBOX_EXECUTE_TIMEOUT,
                command=command,
                args=_redact_args(args),
                timeout=deadline,
            )
            return stdout_bytes, stderr_bytes, True
        return stdout_bytes, stderr_bytes, False

    async def _drain_after_kill(
        self,
        proc: asyncio.subprocess.Process,
        command: str,
        args: tuple[str, ...],
    ) -> tuple[bytes, bytes]:
        """Drain remaining output after killing a process.

        Waits up to 5 seconds for the process to terminate.  If the
        process does not terminate, logs an error and returns empty
        stdout with a diagnostic stderr message.
        """
        try:
            return await asyncio.wait_for(
                proc.communicate(),
                timeout=5.0,
            )
        except TimeoutError:
            logger.exception(
                SANDBOX_KILL_FAILED,
                command=command,
                args=_redact_args(args),
                pid=proc.pid,
                error="process did not terminate 5s after kill",
            )
            return b"", b"[sandbox] process did not terminate after kill"

    async def execute(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path | None = None,
        env_overrides: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> SandboxResult:
        """Execute a command in the sandbox.

        Args:
            command: Executable name or path.
            args: Command arguments.
            cwd: Working directory (defaults to workspace root).
            env_overrides: Extra env vars applied on top of filtered env.
            timeout: Seconds before the process is killed.

        Returns:
            A ``SandboxResult`` with captured output and exit status.

        Raises:
            SandboxStartError: If the subprocess could not be started.
            SandboxError: If cwd is outside the workspace boundary or
                if no safe PATH directories can be determined.
        """
        work_dir = cwd if cwd is not None else self._workspace
        self._validate_cwd(work_dir)

        effective_timeout = (
            timeout if timeout is not None else self._config.timeout_seconds
        )
        env = self._build_filtered_env(env_overrides)

        logger.debug(
            SANDBOX_EXECUTE_START,
            command=command,
            args=_redact_args(args),
            cwd=str(work_dir),
            timeout=effective_timeout,
        )

        proc = await self._spawn_process(command, args, work_dir, env)
        try:
            (
                stdout_bytes,
                stderr_bytes,
                timed_out,
            ) = await self._communicate_with_timeout(
                proc,
                command,
                args,
                effective_timeout,
            )
        finally:
            self._close_process(proc)

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        returncode = proc.returncode if proc.returncode is not None else -1

        if timed_out:
            return SandboxResult(
                stdout=stdout,
                stderr=(stderr or f"Process timed out after {effective_timeout}s"),
                returncode=returncode,
                timed_out=True,
            )

        if returncode != 0:
            logger.warning(
                SANDBOX_EXECUTE_FAILED,
                command=command,
                args=_redact_args(args),
                returncode=returncode,
                stderr=stderr,
            )
        else:
            logger.debug(
                SANDBOX_EXECUTE_SUCCESS,
                command=command,
                args=_redact_args(args),
            )

        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
        )

    async def cleanup(self) -> None:
        """Subprocesses are ephemeral — no resources to release."""
        logger.debug(SANDBOX_CLEANUP, backend="subprocess")

    async def health_check(self) -> bool:
        """Return ``True`` if the workspace directory exists."""
        healthy = self._workspace.is_dir()
        logger.debug(
            SANDBOX_HEALTH_CHECK,
            backend="subprocess",
            healthy=healthy,
            workspace=str(self._workspace),
        )
        return healthy

    def get_backend_type(self) -> NotBlankStr:
        """Return ``'subprocess'``."""
        return NotBlankStr("subprocess")
