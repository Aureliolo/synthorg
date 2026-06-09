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
delegated to the sandbox -- the sandbox handles environment
filtering and workspace boundary enforcement for the ``cwd``,
while ``_BaseGitTool._validate_path`` independently enforces
workspace boundaries for git path arguments.  Git hardening
env vars are passed as ``env_overrides`` to the sandbox.
Without a sandbox, the direct-subprocess path is used.
"""

import os
from abc import ABC
from pathlib import Path
from types import MappingProxyType
from typing import Final

from pydantic import JsonValue

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.git import (
    GIT_COMMAND_FAILED,
    GIT_COMMAND_START,
    GIT_REF_INJECTION_BLOCKED,
    GIT_WORKSPACE_VIOLATION,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools._git_subprocess import (
    _CONTROL_CHAR_RE,
    _await_git_process,
    _process_git_output,
    _sandbox_result_to_execution_result,
    _sanitize_command,
    _start_git_process,
)
from synthorg.tools._process_cleanup import close_subprocess_transport
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.sandbox.errors import SandboxError
from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)

_DEFAULT_TIMEOUT: Final[float] = 30.0

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

# Git discovery env vars that override directory-based repo detection.
# Stripping these ensures the tool always uses the workspace ``cwd`` for
# repo discovery instead of stale env vars (e.g. ``GIT_DIR`` inherited
# from ``git push`` → pre-push hook → agent subprocess chains).
_GIT_DISCOVERY_VARS: Final[frozenset[str]] = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
    }
)


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

    def __init__(  # noqa: PLR0913
        self,
        *,
        name: str,
        description: str,
        parameters_schema: dict[str, JsonValue],
        workspace: Path,
        sandbox: SandboxBackend | None = None,
        action_type: str | None = None,
    ) -> None:
        """Initialize a git tool bound to a workspace.

        Args:
            name: Tool name.
            description: Human-readable description.
            parameters_schema: JSON Schema for tool parameters.
            workspace: Absolute path to the workspace root.
            sandbox: Optional sandbox backend for subprocess isolation.
            action_type: Security action type override.

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
            action_type=action_type,
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
        """Reject flag-like values and control characters.

        Blocks values starting with ``-`` (flag injection) and values
        containing control characters (null bytes, newlines, etc.).

        Args:
            value: The argument string to validate.
            param: Parameter name for the error message.

        Returns:
            A ``ToolExecutionResult`` with ``is_error=True`` if invalid,
            or ``None`` if valid.
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
        if _CONTROL_CHAR_RE.search(value):
            logger.warning(
                GIT_REF_INJECTION_BLOCKED,
                param=param,
                value=repr(value),
            )
            return ToolExecutionResult(
                content=f"Invalid {param}: must not contain control characters",
                is_error=True,
            )
        return None

    @staticmethod
    def _build_git_env() -> dict[str, str]:
        """Build a hardened environment for git subprocesses.

        Applies git hardening overrides, strips git discovery env vars
        (so the tool uses *cwd*-based repo detection), and removes
        obvious secret env vars as defense-in-depth.  For full
        environment filtering, use a ``SandboxBackend``.

        Returns:
            Mapping from ``str`` to ``str``.
        """
        env = {**os.environ, **_GIT_HARDENING_OVERRIDES}
        for key in _GIT_DISCOVERY_VARS:
            env.pop(key, None)
        for key in list(env):
            upper = key.upper()
            if any(sub in upper for sub in _SECRET_SUBSTRINGS):
                del env[key]
        return env

    @staticmethod
    def _build_git_env_overrides() -> dict[str, str]:
        """Return only git-specific hardening env vars.

        Used by the sandbox code path -- the sandbox handles base env
        filtering, and these overrides are applied on top.

        Returns:
            Mapping from ``str`` to ``str``.
        """
        return dict(_GIT_HARDENING_OVERRIDES)

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
        """Execute git through the sandbox backend.

        Returns:
            Result of type ``ToolExecutionResult``.

        Raises:
            RuntimeError: If the operation fails at runtime.
        """
        if self._sandbox is None:  # pragma: no cover -- guarded by caller
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
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # Generic content -- ``ToolExecutionResult.content`` reaches
            # the LLM, so ``str(exc)`` would leak repo URLs / workspace
            # paths past the log scrub above.
            return ToolExecutionResult(
                content="Git command failed in sandbox",
                is_error=True,
            )
        return _sandbox_result_to_execution_result(
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
        """Execute git via direct subprocess (no sandbox).

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        env = self._build_git_env()

        proc_or_err = await _start_git_process(
            args,
            work_dir=work_dir,
            env=env,
        )
        if isinstance(proc_or_err, ToolExecutionResult):
            return proc_or_err

        try:
            output_or_err = await _await_git_process(
                proc_or_err,
                args,
                deadline=deadline,
            )
            if isinstance(output_or_err, ToolExecutionResult):
                return output_or_err

            stdout_bytes, stderr_bytes = output_or_err
            return _process_git_output(
                args,
                proc_or_err.returncode,
                stdout_bytes,
                stderr_bytes,
            )
        finally:
            close_subprocess_transport(proc_or_err)
