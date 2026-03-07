"""Base class for workspace-scoped git tools.

Provides ``_BaseGitTool`` with helper methods for running git
subprocesses, validating relative paths against the workspace
boundary, and rejecting flag-injection attempts.  Subprocess
execution uses ``asyncio.create_subprocess_exec`` (never
``shell=True``) with ``GIT_TERMINAL_PROMPT=0``,
``GIT_CONFIG_NOSYSTEM=1``, ``GIT_CONFIG_GLOBAL`` pointed to the platform null device
(``os.devnull``), and ``GIT_PROTOCOL_FROM_USER=0`` to prevent
interactive prompts and restrict config/protocol attack surfaces.

When a ``SandboxBackend`` is injected, subprocess management is
delegated to the sandbox — the sandbox handles environment
filtering and workspace boundary enforcement for the ``cwd``,
while ``_BaseGitTool._validate_path`` independently enforces
workspace boundaries for git path arguments.  Git hardening
env vars are passed as ``env_overrides`` to the sandbox.
Without a sandbox, the direct-subprocess path is used.
"""

import asyncio
import contextlib
import os
import re
from abc import ABC
from pathlib import Path  # noqa: TC003 — used at runtime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from ai_company.core.enums import ToolCategory
from ai_company.observability import get_logger
from ai_company.observability.events.git import (
    GIT_COMMAND_FAILED,
    GIT_COMMAND_START,
    GIT_COMMAND_SUCCESS,
    GIT_COMMAND_TIMEOUT,
    GIT_REF_INJECTION_BLOCKED,
    GIT_WORKSPACE_VIOLATION,
)
from ai_company.tools._process_cleanup import close_subprocess_transport
from ai_company.tools.base import BaseTool, ToolExecutionResult
from ai_company.tools.sandbox.errors import SandboxError

if TYPE_CHECKING:
    from ai_company.tools.sandbox.protocol import SandboxBackend
    from ai_company.tools.sandbox.result import SandboxResult

logger = get_logger(__name__)

_DEFAULT_TIMEOUT: Final[float] = 30.0

# Matches http(s)://userinfo@host patterns in git URLs.
_CREDENTIAL_RE = re.compile(r"(https?://)[^@/]+@")

_GIT_HARDENING_OVERRIDES: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_PROTOCOL_FROM_USER": "0",
    }
)

# Substrings that indicate secret env vars (defense-in-depth for direct path).
_SECRET_SUBSTRINGS: Final[tuple[str, ...]] = (
    "KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "CREDENTIAL",
    "PRIVATE",
)


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]+")
_MAX_STDERR_FRAGMENT: Final[int] = 500


def _sanitize_command(args: list[str]) -> list[str]:
    """Redact embedded credentials from git command args for logging."""
    return [_CREDENTIAL_RE.sub(r"\1***@", a) for a in args]


def _sanitize_stderr(raw: str) -> str:
    """Replace control characters, redact credentials, and truncate.

    All control characters (including newlines, tabs, and carriage
    returns) are collapsed into single spaces to prevent log injection
    and LLM prompt injection via stderr content.  Embedded credentials
    (``https://user:token@host``) are redacted before truncation.
    """
    sanitized = _CONTROL_CHAR_RE.sub(" ", raw).strip()
    return _CREDENTIAL_RE.sub(r"\1***@", sanitized)[:_MAX_STDERR_FRAGMENT]


class _BaseGitTool(BaseTool, ABC):
    """Shared base for all git tools.

    Holds the ``workspace`` path and provides helper methods for running
    git commands and validating relative paths against the workspace
    boundary.

    When a ``SandboxBackend`` is provided, ``_run_git`` delegates
    subprocess management to the sandbox.  Without a sandbox, the
    existing direct-subprocess logic is used (backward compatible).

    Attributes:
        workspace: Absolute path to the agent's workspace directory.
    """

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters_schema: dict[str, Any],
        workspace: Path,
        sandbox: SandboxBackend | None = None,
    ) -> None:
        """Initialize a git tool bound to a workspace.

        Args:
            name: Tool name.
            description: Human-readable description.
            parameters_schema: JSON Schema for tool parameters.
            workspace: Absolute path to the workspace root.
            sandbox: Optional sandbox backend for subprocess isolation.

        Raises:
            ValueError: If *workspace* is not an absolute path.
        """
        if not workspace.is_absolute():
            msg = f"workspace must be an absolute path, got: {workspace}"
            raise ValueError(msg)
        super().__init__(
            name=name,
            description=description,
            parameters_schema=parameters_schema,
            category=ToolCategory.VERSION_CONTROL,
        )
        self._workspace = workspace.resolve()
        self._sandbox = sandbox

    @property
    def workspace(self) -> Path:
        """Workspace root directory."""
        return self._workspace

    def _validate_path(self, relative: str) -> Path:
        """Resolve a relative path and verify it stays within workspace.

        Args:
            relative: A relative path string from the LLM.

        Returns:
            The resolved absolute ``Path``.

        Raises:
            ValueError: If the path escapes the workspace boundary or
                cannot be resolved.
        """
        try:
            resolved = (self._workspace / relative).resolve()
        except OSError as exc:
            logger.warning(
                GIT_WORKSPACE_VIOLATION,
                path=relative,
                workspace=str(self._workspace),
                error="path resolution failed",
            )
            msg = f"Path '{relative}' could not be resolved"
            raise ValueError(msg) from exc
        try:
            resolved.relative_to(self._workspace)
        except ValueError as exc:
            logger.warning(
                GIT_WORKSPACE_VIOLATION,
                path=relative,
                workspace=str(self._workspace),
            )
            msg = f"Path '{relative}' is outside workspace"
            raise ValueError(msg) from exc
        return resolved

    def _check_paths(self, paths: list[str]) -> ToolExecutionResult | None:
        """Validate a list of paths, returning an error result or None.

        Args:
            paths: Relative path strings to validate.

        Returns:
            A ``ToolExecutionResult`` with ``is_error=True`` if any path
            escapes the workspace, or ``None`` if all paths are valid.
        """
        for p in paths:
            try:
                self._validate_path(p)
            except ValueError as exc:
                return ToolExecutionResult(
                    content=str(exc),
                    is_error=True,
                )
        return None

    def _check_git_arg(
        self,
        value: str,
        *,
        param: str,
    ) -> ToolExecutionResult | None:
        """Reject values starting with ``-`` to prevent flag injection.

        Used for refs, branch names, author filters, date strings, and
        any other git argument that must not be interpreted as a flag.

        Args:
            value: The argument string to validate.
            param: Parameter name for the error message.

        Returns:
            A ``ToolExecutionResult`` with ``is_error=True`` if the value
            starts with ``-``, or ``None`` if valid.
        """
        if value.startswith("-"):
            logger.warning(
                GIT_REF_INJECTION_BLOCKED,
                param=param,
                value=value,
            )
            return ToolExecutionResult(
                content=f"Invalid {param}: must not start with '-'",
                is_error=True,
            )
        return None

    @staticmethod
    def _build_git_env() -> dict[str, str]:
        """Build a hardened environment for git subprocesses.

        Applies git hardening overrides and strips obvious secret
        env vars as defense-in-depth.  For full environment filtering,
        use a ``SandboxBackend``.
        """
        env = {**os.environ, **_GIT_HARDENING_OVERRIDES}
        for key in list(env):
            upper = key.upper()
            if any(sub in upper for sub in _SECRET_SUBSTRINGS):
                del env[key]
        return env

    @staticmethod
    def _build_git_env_overrides() -> dict[str, str]:
        """Return only git-specific hardening env vars.

        Used by the sandbox code path — the sandbox handles base env
        filtering, and these overrides are applied on top.
        """
        return dict(_GIT_HARDENING_OVERRIDES)

    async def _start_git_process(
        self,
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
            logger.warning(
                GIT_COMMAND_FAILED,
                command=_sanitize_command(["git", *args]),
                error=f"subprocess start failed: {exc}",
                exc_info=True,
            )
            return ToolExecutionResult(
                content=f"Failed to start git process: {exc}",
                is_error=True,
            )

    async def _await_git_process(
        self,
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
                    timeout=5.0,
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

    @staticmethod
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
            logger.warning(
                GIT_COMMAND_FAILED,
                command=_sanitize_command(["git", *args]),
                returncode=returncode,
                stderr=stderr,
                stdout=stdout,
            )
            return ToolExecutionResult(
                content=stderr or stdout or "Unknown git error",
                is_error=True,
            )
        logger.debug(
            GIT_COMMAND_SUCCESS,
            command=_sanitize_command(["git", *args]),
        )
        return ToolExecutionResult(content=stdout)

    @staticmethod
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
            logger.warning(
                GIT_COMMAND_FAILED,
                command=_sanitize_command(["git", *args]),
                returncode=result.returncode,
                stderr=result.stderr,
                stdout=result.stdout,
            )
            return ToolExecutionResult(
                content=(result.stderr or result.stdout or "Unknown git error"),
                is_error=True,
            )
        logger.debug(
            GIT_COMMAND_SUCCESS,
            command=_sanitize_command(["git", *args]),
        )
        return ToolExecutionResult(content=result.stdout)

    async def _run_git(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        deadline: float = _DEFAULT_TIMEOUT,
    ) -> ToolExecutionResult:
        """Run a git subprocess and return the result.

        When a sandbox backend is available, delegates execution to it.
        Otherwise uses the direct subprocess path.

        Args:
            args: Arguments to pass after ``git``.
            cwd: Working directory (defaults to workspace).
            deadline: Seconds before the process is killed.

        Returns:
            A ``ToolExecutionResult`` with stdout on success, or an
            error message with ``is_error=True`` on failure.
        """
        work_dir = cwd or self._workspace

        logger.debug(
            GIT_COMMAND_START,
            command=_sanitize_command(["git", *args]),
            cwd=str(work_dir),
        )

        if self._sandbox is not None:
            return await self._run_git_sandboxed(args, work_dir, deadline)

        return await self._run_git_direct(args, work_dir, deadline)

    async def _run_git_sandboxed(
        self,
        args: list[str],
        work_dir: Path,
        deadline: float,
    ) -> ToolExecutionResult:
        """Execute git through the sandbox backend."""
        if self._sandbox is None:  # pragma: no cover — guarded by caller
            msg = "_run_git_sandboxed called without sandbox"
            raise RuntimeError(msg)

        try:
            result = await self._sandbox.execute(
                command="git",
                args=tuple(args),
                cwd=work_dir,
                env_overrides=self._build_git_env_overrides(),
                timeout=deadline,
            )
        except SandboxError as exc:
            logger.warning(
                GIT_COMMAND_FAILED,
                command=_sanitize_command(["git", *args]),
                error=str(exc),
            )
            return ToolExecutionResult(
                content=str(exc),
                is_error=True,
            )
        return self._sandbox_result_to_execution_result(
            args,
            result,
            deadline=deadline,
        )

    async def _run_git_direct(
        self,
        args: list[str],
        work_dir: Path,
        deadline: float,
    ) -> ToolExecutionResult:
        """Execute git via direct subprocess (no sandbox)."""
        env = self._build_git_env()

        proc_or_err = await self._start_git_process(
            args,
            work_dir=work_dir,
            env=env,
        )
        if isinstance(proc_or_err, ToolExecutionResult):
            return proc_or_err

        try:
            output_or_err = await self._await_git_process(
                proc_or_err,
                args,
                deadline=deadline,
            )
            if isinstance(output_or_err, ToolExecutionResult):
                return output_or_err

            stdout_bytes, stderr_bytes = output_or_err
            return self._process_git_output(
                args,
                proc_or_err.returncode,
                stdout_bytes,
                stderr_bytes,
            )
        finally:
            close_subprocess_transport(proc_or_err)
