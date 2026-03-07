"""Base class for workspace-scoped git tools.

Provides ``_BaseGitTool`` with helper methods for running git
subprocesses, validating relative paths against the workspace
boundary, and rejecting flag-injection attempts.  Subprocess
execution uses ``asyncio.create_subprocess_exec`` (never
``shell=True``) with ``GIT_TERMINAL_PROMPT=0``,
``GIT_CONFIG_NOSYSTEM=1``, ``GIT_CONFIG_GLOBAL`` pointed to
``/dev/null``, and ``GIT_PROTOCOL_FROM_USER=0`` to prevent
interactive prompts and restrict config/protocol attack surfaces.
"""

import asyncio
import os
import re
from abc import ABC
from pathlib import Path  # noqa: TC003 — used at runtime
from typing import Any, Final

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
from ai_company.tools.base import BaseTool, ToolExecutionResult

logger = get_logger(__name__)

_DEFAULT_TIMEOUT: Final[float] = 30.0

_CREDENTIAL_RE = re.compile(r"(https?://)[^@/]+@")


def _sanitize_command(args: list[str]) -> list[str]:
    """Redact embedded credentials from git command args for logging."""
    return [_CREDENTIAL_RE.sub(r"\1***@", a) for a in args]


class _BaseGitTool(BaseTool, ABC):
    """Shared base for all git tools.

    Holds the ``workspace`` path and provides helper methods for running
    git commands and validating relative paths against the workspace
    boundary.

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
    ) -> None:
        """Initialize a git tool bound to a workspace.

        Args:
            name: Tool name.
            description: Human-readable description.
            parameters_schema: JSON Schema for tool parameters.
            workspace: Absolute path to the workspace root.

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
        except OSError:
            logger.warning(
                GIT_WORKSPACE_VIOLATION,
                path=relative,
                workspace=str(self._workspace),
                error="path resolution failed",
            )
            msg = f"Path '{relative}' could not be resolved"
            raise ValueError(msg) from None
        try:
            resolved.relative_to(self._workspace)
        except ValueError:
            logger.warning(
                GIT_WORKSPACE_VIOLATION,
                path=relative,
                workspace=str(self._workspace),
            )
            msg = f"Path '{relative}' is outside workspace"
            raise ValueError(msg) from None
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

    def _check_ref(
        self,
        value: str,
        *,
        param: str,
    ) -> ToolExecutionResult | None:
        """Reject values starting with ``-`` to prevent flag injection.

        Args:
            value: The ref or branch name string to validate.
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
        """Build a hardened environment for git subprocesses."""
        return {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_PROTOCOL_FROM_USER": "0",
        }

    async def _start_git_process(
        self,
        args: list[str],
        *,
        work_dir: Path,
        env: dict[str, str],
    ) -> asyncio.subprocess.Process | ToolExecutionResult:
        """Start the git subprocess, returning an error on failure."""
        try:
            return await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=work_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError:
            logger.warning(
                GIT_COMMAND_FAILED,
                command=_sanitize_command(["git", *args]),
                error="subprocess start failed",
                exc_info=True,
            )
            return ToolExecutionResult(
                content="Failed to start git process",
                is_error=True,
            )

    async def _await_git_process(
        self,
        proc: asyncio.subprocess.Process,
        args: list[str],
        *,
        deadline: float,
    ) -> tuple[bytes, bytes] | ToolExecutionResult:
        """Wait for the process with a timeout, returning output or error."""
        try:
            return await asyncio.wait_for(
                proc.communicate(),
                timeout=deadline,
            )
        except TimeoutError:
            proc.kill()
            try:
                await asyncio.wait_for(proc.communicate(), timeout=5.0)
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
            )
            return ToolExecutionResult(
                content=f"Git command timed out after {deadline}s",
                is_error=True,
            )

    @staticmethod
    def _process_git_output(
        args: list[str],
        returncode: int | None,
        stdout_bytes: bytes,
        stderr_bytes: bytes,
    ) -> ToolExecutionResult:
        """Decode output and build the result."""
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

    async def _run_git(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        deadline: float = _DEFAULT_TIMEOUT,
    ) -> ToolExecutionResult:
        """Run a git subprocess and return the result.

        Args:
            args: Arguments to pass after ``git``.
            cwd: Working directory (defaults to workspace).
            deadline: Seconds before the process is killed.

        Returns:
            A ``ToolExecutionResult`` with stdout on success, or an
            error message with ``is_error=True`` on failure.
        """
        work_dir = cwd or self._workspace
        env = self._build_git_env()

        logger.debug(
            GIT_COMMAND_START,
            command=_sanitize_command(["git", *args]),
            cwd=str(work_dir),
        )

        proc_or_err = await self._start_git_process(
            args,
            work_dir=work_dir,
            env=env,
        )
        if isinstance(proc_or_err, ToolExecutionResult):
            return proc_or_err

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
